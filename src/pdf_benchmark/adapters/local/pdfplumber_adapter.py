from __future__ import annotations

from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import BaseLocalAdapter, ToolExecutionError, ToolOutputParseError
from pdf_benchmark.models import ImageObject, Page, RawToolResult, StandardizedDocument, Table, TableCell, TextBlock
from pdf_benchmark.utils.geometry import bbox_sort_key, normalize_bbox
from pdf_benchmark.utils.io import read_json, relative_to_or_name, write_json
from pdf_benchmark.utils.versions import installed_versions
from pdf_benchmark.utils.text import collapse_ws


class PdfPlumberAdapter(BaseLocalAdapter):
    tool_name = "pdfplumber"
    distribution_name = "pdfplumber"
    pinned_version = "0.11.10"

    DEFAULT_CONFIG = {
        "x_tolerance": 3,
        "y_tolerance": 3,
        "use_text_flow": False,
        "find_tables": True,
        "table_settings": {},
        "resource_sample_interval": 0.1,
    }

    def model_versions(self) -> dict[str, str]:
        return installed_versions(['pdfplumber', 'pdfminer.six'])

    def __init__(self, config: dict[str, Any] | None = None, logger=None):
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(config or {})
        super().__init__(merged, logger=logger)

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        try:
            import pdfplumber
        except Exception as exc:  # pragma: no cover
            raise ToolExecutionError("pdfplumber is not installed") from exc

        pages: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for idx, page in enumerate(pdf.pages):
                    # Preserve word-level geometry because extract_text_lines()
                    # can merge neighbouring columns into one wide line.
                    try:
                        words = page.extract_words(
                            x_tolerance=float(self.config["x_tolerance"]),
                            y_tolerance=float(self.config["y_tolerance"]),
                            use_text_flow=bool(self.config["use_text_flow"]),
                        ) or []
                    except Exception as exc:
                        warnings.append(f"page {idx + 1}: extract_words failed: {exc}")
                        words = []

                    try:
                        lines = page.extract_text_lines(
                            x_tolerance=float(self.config["x_tolerance"]),
                            y_tolerance=float(self.config["y_tolerance"]),
                            layout=False,
                            strip=True,
                            return_chars=False,
                        ) or []
                    except Exception as exc:
                        warnings.append(
                            f"page {idx + 1}: extract_text_lines failed ({exc}); "
                            "using word-level text only"
                        )
                        lines = [
                            {
                                "text": w.get("text", ""),
                                "x0": w.get("x0"),
                                "x1": w.get("x1"),
                                "top": w.get("top"),
                                "bottom": w.get("bottom"),
                            }
                            for w in words
                        ]

                    tables: list[dict[str, Any]] = []
                    if self.config["find_tables"]:
                        try:
                            found = page.find_tables(table_settings=self.config.get("table_settings") or {})
                            for t in found:
                                matrix = t.extract() or []
                                rows = []
                                for r in getattr(t, "rows", []) or []:
                                    rcells = []
                                    for cell in getattr(r, "cells", []) or []:
                                        if cell is None:
                                            rcells.append(None)
                                        else:
                                            try:
                                                rcells.append(list(cell))
                                            except Exception:
                                                rcells.append(None)
                                    rows.append(rcells)
                                tables.append(
                                    {
                                        "bbox": list(t.bbox) if t.bbox is not None else None,
                                        "data": matrix,
                                        "row_cell_bboxes": rows,
                                    }
                                )
                        except Exception as exc:
                            warnings.append(f"page {idx + 1}: find_tables failed: {exc}")

                    images: list[dict[str, Any]] = []
                    for im in page.images:
                        images.append(
                            {
                                k: im.get(k)
                                for k in (
                                    "x0",
                                    "x1",
                                    "top",
                                    "bottom",
                                    "width",
                                    "height",
                                    "name",
                                    "srcsize",
                                    "bits",
                                    "colorspace",
                                    "imagemask",
                                    "object_type",
                                )
                                if k in im
                            }
                        )

                    pages.append(
                        {
                            "page_number": idx + 1,
                            "width": float(page.width),
                            "height": float(page.height),
                            "words": words,
                            "lines": lines,
                            "tables": tables,
                            "images": images,
                        }
                    )
        except Exception as exc:
            raise ToolExecutionError(f"pdfplumber failed on {pdf_path.name}: {exc}") from exc

        raw_path = write_json(
            raw_dir / "pdfplumber_raw.json",
            {"tool": self.tool_name, "version": self.installed_version(), "config": self.config, "pages": pages},
        )
        return RawToolResult(
            primary_artifact=str(raw_path),
            artifacts=[relative_to_or_name(raw_path, raw_dir.parent)],
            metadata={"page_count": len(pages)},
            warnings=warnings,
        )

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
            raise ToolOutputParseError("pdfplumber raw artifact is missing")
        raw = read_json(raw_result.primary_artifact)
        pages_out: list[Page] = []

        for page_raw in raw.get("pages", []):
            pn = int(page_raw["page_number"])
            width = float(page_raw["width"])
            height = float(page_raw["height"])
            page = Page(page_number=pn, width=width, height=height)
            ordered: list[tuple[tuple[float, float, int], str]] = []
            counter = 0

            text_items = page_raw.get("words") or page_raw.get("lines") or []
            text_source = "extract_words" if page_raw.get("words") else "extract_text_lines"

            for i, item in enumerate(text_items):
                text = collapse_ws(item.get("text"))
                if not text:
                    continue
                bbox_raw = [item.get("x0"), item.get("top"), item.get("x1"), item.get("bottom")]
                if any(v is None for v in bbox_raw):
                    bbox = None
                else:
                    bbox = normalize_bbox(bbox_raw, width, height)
                kind = "word" if text_source == "extract_words" else "text"
                eid = f"p{pn}_{kind}_{i}"
                page.text_blocks.append(
                    TextBlock(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        raw_text=text,
                        order_index=counter,
                        provenance={"source": text_source},
                    )
                )
                ordered.append((bbox_sort_key(bbox, counter), eid))
                counter += 1

            for i, tbl in enumerate(page_raw.get("tables", []) or []):
                matrix = tbl.get("data") or []
                rows = len(matrix)
                cols = max((len(r) for r in matrix), default=0)
                bbox = normalize_bbox(tbl.get("bbox"), width, height)
                row_bboxes = tbl.get("row_cell_bboxes") or []
                cells: list[TableCell] = []
                for r in range(rows):
                    for c in range(cols):
                        value = matrix[r][c] if c < len(matrix[r]) and matrix[r][c] is not None else ""
                        cb = None
                        if r < len(row_bboxes) and c < len(row_bboxes[r]) and row_bboxes[r][c] is not None:
                            cb = normalize_bbox(row_bboxes[r][c], width, height)
                        cells.append(TableCell(row_index=r, column_index=c, text=str(value), bbox=cb))
                eid = f"p{pn}_table_{i}"
                page.tables.append(
                    Table(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        rows=rows,
                        columns=cols,
                        cells=cells,
                        order_index=counter,
                        provenance={"merged_cells": "not explicitly exposed; spans are not inferred"},
                    )
                )
                ordered.append((bbox_sort_key(bbox, counter), eid))
                counter += 1

            # pdfplumber exposes image objects/geometry, but does not provide a high-level
            # image reconstruction API. Preserve detection/geometry without pretending
            # extraction succeeded.
            for i, im in enumerate(page_raw.get("images", []) or []):
                vals = [im.get("x0"), im.get("top"), im.get("x1"), im.get("bottom")]
                bbox = None if any(v is None for v in vals) else normalize_bbox(vals, width, height)
                eid = f"p{pn}_image_{i}"
                page.images.append(
                    ImageObject(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        extraction_success=False,
                        order_index=counter,
                        provenance=im,
                    )
                )
                ordered.append((bbox_sort_key(bbox, counter), eid))
                counter += 1

            page.reading_order = [eid for _, eid in sorted(ordered, key=lambda x: x[0])]
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
                    "images": "metadata/geometry only",
                    "diagrams": False,
                    "reading_order": "PDF layout/extraction heuristic",
                    "ocr": False,
                }
            },
        )
