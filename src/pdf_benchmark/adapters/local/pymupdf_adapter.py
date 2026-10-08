from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import BaseLocalAdapter, ToolExecutionError, ToolOutputParseError
from pdf_benchmark.models import (
    ImageObject,
    Page,
    RawToolResult,
    StandardizedDocument,
    Table,
    TableCell,
    TextBlock,
)
from pdf_benchmark.utils.geometry import normalize_bbox
from pdf_benchmark.utils.io import read_json, relative_to_or_name, write_json
from pdf_benchmark.utils.versions import installed_versions
from pdf_benchmark.utils.text import collapse_ws


class PyMuPDFAdapter(BaseLocalAdapter):
    tool_name = "pymupdf"
    distribution_name = "PyMuPDF"
    pinned_version = "1.28.2"

    DEFAULT_CONFIG = {
        "sort": True,
        "ocr_if_no_text": True,
        "ocr_languages": "rus+eng",
        "ocr_dpi": 300,
        "min_native_text_chars": 20,
        "find_tables": True,
        "table_use_layout": True,
        "table_union": False,
        "table_refine": False,
        "resource_sample_interval": 0.1,
    }

    def model_versions(self) -> dict[str, str]:
        return installed_versions(['PyMuPDF'])

    def __init__(self, config: dict[str, Any] | None = None, logger=None):
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(config or {})
        super().__init__(merged, logger=logger)

    @staticmethod
    def _serialize_table(table: Any) -> dict[str, Any]:
        try:
            data = table.extract()
        except Exception:
            data = []
        cells = []
        for cell in getattr(table, "cells", []) or []:
            if cell is None:
                cells.append(None)
            elif hasattr(cell, "x0"):
                cells.append([cell.x0, cell.y0, cell.x1, cell.y1])
            else:
                try:
                    cells.append(list(cell))
                except Exception:
                    cells.append(None)
        row_cells = []
        for row in getattr(table, "rows", []) or []:
            current = []
            for cell in getattr(row, "cells", []) or []:
                if cell is None:
                    current.append(None)
                elif hasattr(cell, "x0"):
                    current.append([cell.x0, cell.y0, cell.x1, cell.y1])
                else:
                    try:
                        current.append(list(cell))
                    except Exception:
                        current.append(None)
            row_cells.append(current)
        header = None
        h = getattr(table, "header", None)
        if h is not None:
            header = {
                "names": list(getattr(h, "names", []) or []),
                "external": bool(getattr(h, "external", False)),
                "bbox": list(getattr(h, "bbox", []) or []) if getattr(h, "bbox", None) else None,
            }
        bbox = getattr(table, "bbox", None)
        return {
            "bbox": list(bbox) if bbox is not None else None,
            "row_count": int(getattr(table, "row_count", len(data) if data else 0) or 0),
            "col_count": int(getattr(table, "col_count", max((len(r) for r in data), default=0)) or 0),
            "data": data,
            "cells": cells,
            "row_cells": row_cells,
            "header": header,
        }

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        try:
            import pymupdf
        except Exception as exc:  # pragma: no cover - environment specific
            raise ToolExecutionError("PyMuPDF is not installed") from exc

        pages: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            doc = pymupdf.open(pdf_path)
            for idx, page in enumerate(doc):
                native = page.get_text("text") or ""
                textpage = None
                ocr_used = False
                if self.config["ocr_if_no_text"] and len("".join(native.split())) < int(self.config["min_native_text_chars"]):
                    try:
                        textpage = page.get_textpage_ocr(
                            language=str(self.config["ocr_languages"]),
                            dpi=int(self.config["ocr_dpi"]),
                            full=True,
                        )
                        ocr_used = True
                    except Exception as exc:
                        warnings.append(f"page {idx + 1}: PyMuPDF OCR unavailable/failed: {exc}")

                kwargs = {"sort": bool(self.config["sort"])}
                if textpage is not None:
                    kwargs["textpage"] = textpage
                page_json = json.loads(page.get_text("json", **kwargs))

                tables: list[dict[str, Any]] = []
                if self.config["find_tables"]:
                    try:
                        finder = page.find_tables(
                            use_layout=bool(self.config["table_use_layout"]),
                            union=bool(self.config["table_union"]),
                            refine=bool(self.config["table_refine"]),
                        )
                        tables = [self._serialize_table(t) for t in finder.tables]
                    except Exception as exc:
                        warnings.append(f"page {idx + 1}: find_tables failed: {exc}")

                pages.append(
                    {
                        "page_number": idx + 1,
                        "width": float(page.rect.width),
                        "height": float(page.rect.height),
                        "ocr_used": ocr_used,
                        "text_json": page_json,
                        "tables": tables,
                    }
                )
            doc.close()
        except Exception as exc:
            raise ToolExecutionError(f"PyMuPDF failed on {pdf_path.name}: {exc}") from exc

        raw_path = write_json(
            raw_dir / "pymupdf_raw.json",
            {
                "tool": self.tool_name,
                "version": self.installed_version(),
                "config": self.config,
                "pages": pages,
            },
        )
        return RawToolResult(
            primary_artifact=str(raw_path),
            artifacts=[relative_to_or_name(raw_path, raw_dir.parent)],
            metadata={"page_count": len(pages)},
            warnings=warnings,
        )

    @staticmethod
    def _decode_image(block: dict[str, Any], path: Path) -> bool:
        payload = block.get("image")
        if not payload:
            return False
        try:
            if isinstance(payload, str):
                if payload.startswith("data:") and ";base64," in payload:
                    payload = payload.split(";base64,", 1)[1]
                data = base64.b64decode(payload)
            elif isinstance(payload, (bytes, bytearray)):
                data = bytes(payload)
            else:
                return False
            path.write_bytes(data)
            return True
        except Exception:
            return False

    @staticmethod
    def _line_bbox(line: dict[str, Any], block: dict[str, Any]) -> list[float] | None:
        """Use native line geometry, then the union of spans, then the block."""
        value = line.get("bbox")
        if isinstance(value, (list, tuple)) and len(value) == 4:
            return [float(v) for v in value]

        boxes = [
            span.get("bbox")
            for span in (line.get("spans") or [])
            if isinstance(span.get("bbox"), (list, tuple)) and len(span["bbox"]) == 4
        ]
        if boxes:
            return [
                min(float(box[0]) for box in boxes),
                min(float(box[1]) for box in boxes),
                max(float(box[2]) for box in boxes),
                max(float(box[3]) for box in boxes),
            ]

        value = block.get("bbox")
        if isinstance(value, (list, tuple)) and len(value) == 4:
            return [float(v) for v in value]
        return None

    def standardize(
        self,
        raw_result: RawToolResult,
        raw_dir: Path,
        assets_dir: Path,
        *,
        document_id: str,
        pdf_path: Path,
    ) -> StandardizedDocument:
        if not raw_result.primary_artifact:
            raise ToolOutputParseError("PyMuPDF raw artifact is missing")
        raw = read_json(raw_result.primary_artifact)
        pages_out: list[Page] = []

        for page_raw in raw.get("pages", []):
            pn = int(page_raw["page_number"])
            width = float(page_raw["width"])
            height = float(page_raw["height"])
            page = Page(page_number=pn, width=width, height=height)
            reading_order: list[str] = []
            counter = 0

            text_json = page_raw.get("text_json") or {}
            for b_idx, block in enumerate(text_json.get("blocks", []) or []):
                btype = block.get("type")
                bbox = normalize_bbox(block.get("bbox"), width, height)
                if btype == 0:
                    block_order = counter
                    block_emitted = False
                    for line_idx, line in enumerate(block.get("lines", []) or []):
                        spans = line.get("spans", []) or []
                        # Span boundaries normally mean a font/style change, not
                        # a word boundary. PyMuPDF already keeps real whitespace
                        # in span text, so concatenate it exactly before the
                        # conservative whitespace normalization.
                        text = collapse_ws("".join(
                            str(span.get("text") or "") for span in spans
                        ))
                        if not text:
                            continue
                        raw_line_bbox = self._line_bbox(line, block)
                        line_bbox = normalize_bbox(raw_line_bbox, width, height)
                        eid = f"p{pn}_text_{b_idx}_line_{line_idx}"
                        item = TextBlock(
                            element_id=eid,
                            page_number=pn,
                            bbox=line_bbox,
                            raw_text=text,
                            # Every line belongs to the same native text block.
                            # Keeping its former block order also keeps image and
                            # table order indices stable across re-standardization.
                            order_index=block_order,
                            provenance={
                                "pymupdf_block_number": block.get("number"),
                                "pymupdf_block_index": b_idx,
                                "pymupdf_line_index": line_idx,
                                "line_direction": line.get("dir"),
                                "writing_mode": line.get("wmode"),
                                "spans": spans,
                                "text_assembly": "native_span_concatenation_v1",
                                "ocr_used": page_raw.get("ocr_used", False),
                            },
                        )
                        page.text_blocks.append(item)
                        reading_order.append(eid)
                        block_emitted = True
                    if block_emitted:
                        counter += 1
                elif btype == 1:
                    ext = str(block.get("ext") or "bin").lower()
                    asset = assets_dir / f"p{pn}_image_{b_idx}.{ext}"
                    ok = self._decode_image(block, asset)
                    eid = f"p{pn}_image_{b_idx}"
                    image = ImageObject(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        asset_path=relative_to_or_name(asset, assets_dir.parent) if ok else None,
                        extraction_success=ok,
                        order_index=counter,
                        provenance={
                            "width": block.get("width"),
                            "height": block.get("height"),
                            "xres": block.get("xres"),
                            "yres": block.get("yres"),
                        },
                    )
                    page.images.append(image)
                    reading_order.append(eid)
                    counter += 1

            for t_idx, tbl in enumerate(page_raw.get("tables", []) or []):
                bbox = normalize_bbox(tbl.get("bbox"), width, height)
                matrix = tbl.get("data") or []
                rows = int(tbl.get("row_count") or len(matrix))
                cols = int(tbl.get("col_count") or max((len(r) for r in matrix), default=0))
                header = tbl.get("header") or {}
                internal_header = bool(header and not header.get("external") and header.get("names"))
                cells: list[TableCell] = []
                row_cells = tbl.get("row_cells") or []
                for r, row in enumerate(matrix):
                    for c in range(cols):
                        value = row[c] if c < len(row) and row[c] is not None else ""
                        cb = None
                        if r < len(row_cells) and c < len(row_cells[r]) and row_cells[r][c] is not None:
                            cb = normalize_bbox(row_cells[r][c], width, height)
                        cells.append(
                            TableCell(
                                row_index=r,
                                column_index=c,
                                text=str(value),
                                is_header=(internal_header and r == 0),
                                bbox=cb,
                            )
                        )
                eid = f"p{pn}_table_{t_idx}"
                table = Table(
                    element_id=eid,
                    page_number=pn,
                    bbox=bbox,
                    rows=rows,
                    columns=cols,
                    cells=cells,
                    order_index=counter,
                    provenance={"header": header},
                )
                page.tables.append(table)
                reading_order.append(eid)
                counter += 1

            # get_text(..., sort=True) already defines the native text order.
            # A second coordinate sort breaks complex formula/glyph sequences.
            page.reading_order = reading_order
            pages_out.append(page)

        return StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path),
            tool=self.tool_metadata(),
            pages=pages_out,
            metadata={
                "capabilities": {
                    "text": True,
                    "tables": True,
                    "formulas": False,
                    "chemical_objects": False,
                    "images": True,
                    "diagrams": False,
                    "reading_order": "PyMuPDF get_text(sort=True) native block/line order; tables appended",
                    "ocr": "Tesseract integration when configured/available",
                }
            },
        )
