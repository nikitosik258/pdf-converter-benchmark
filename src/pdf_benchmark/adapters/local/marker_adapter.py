from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup

from pdf_benchmark.adapters.base import BaseLocalAdapter, ToolExecutionError, ToolOutputParseError
from pdf_benchmark.models import Caption, DiagramObject, Formula, ImageObject, Page, RawToolResult, StandardizedDocument, Table, TextBlock
from pdf_benchmark.utils.geometry import normalize_bbox
from pdf_benchmark.utils.html_tables import parse_html_table
from pdf_benchmark.utils.io import read_json, relative_to_or_name, write_json
from pdf_benchmark.utils.versions import installed_versions
from pdf_benchmark.utils.text import collapse_ws, html_to_text, strip_math_wrappers


class MarkerAdapter(BaseLocalAdapter):
    tool_name = "marker"
    distribution_name = "marker-pdf"
    pinned_version = "2.0.0"

    DEFAULT_CONFIG = {
        "mode": "balanced",
        "output_format": "json",
        "use_llm": False,
        "disable_ocr": False,
        "force_ocr": False,
        "ocr_inline_math": True,
        "disable_image_extraction": False,
        "keep_pageheader_in_output": True,
        "keep_pagefooter_in_output": True,
        "resource_sample_interval": 0.2,
    }

    def model_versions(self) -> dict[str, str]:
        return installed_versions(['marker-pdf', 'surya-ocr', 'torch'])

    def __init__(self, config: dict[str, Any] | None = None, logger=None):
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(config or {})
        # Benchmark invariant: external LLM augmentation is forbidden.
        merged["use_llm"] = False
        merged["output_format"] = "json"
        super().__init__(merged, logger=logger)

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        try:
            from marker.config.parser import ConfigParser
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
        except Exception as exc:  # pragma: no cover
            raise ToolExecutionError("marker-pdf is not installed") from exc

        try:
            parser = ConfigParser(self.config)
            converter = PdfConverter(
                config=parser.generate_config_dict(),
                artifact_dict=create_model_dict(),
                processor_list=parser.get_processors(),
                renderer=parser.get_renderer(),
                llm_service=parser.get_llm_service(),
            )
            rendered = converter(str(pdf_path))
            if not hasattr(rendered, "model_dump"):
                raise TypeError(f"Unexpected Marker result type: {type(rendered)!r}")
            payload = rendered.model_dump(mode="json")
        except Exception as exc:
            raise ToolExecutionError(f"Marker conversion failed on {pdf_path.name}: {exc}") from exc

        raw_path = write_json(raw_dir / "marker_raw.json", payload)
        return RawToolResult(
            primary_artifact=str(raw_path),
            artifacts=[relative_to_or_name(raw_path, raw_dir.parent)],
            metadata={"page_count": len(payload.get("children", []) or []), "marker_metadata": payload.get("metadata", {})},
        )

    @staticmethod
    def _type_name(value: Any) -> str:
        name = str(value or "")
        if "." in name:
            name = name.split(".")[-1]
        return name

    @classmethod
    def _leaf_blocks(cls, block: dict[str, Any]) -> Iterable[dict[str, Any]]:
        children = block.get("children")
        if children:
            for child in children:
                yield from cls._leaf_blocks(child)
        else:
            yield block

    @staticmethod
    def _math_from_html(html: str | None) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        math = soup.find("math")
        value = math.get_text(" ", strip=True) if math else soup.get_text(" ", strip=True)
        return strip_math_wrappers(collapse_ws(value))

    @staticmethod
    def _save_images(images: dict[str, Any] | None, assets_dir: Path, stem: str) -> str | None:
        if not images:
            return None
        for _key, payload in images.items():
            try:
                ext = "png"
                if isinstance(payload, str):
                    data_str = payload
                    if data_str.startswith("data:") and ";base64," in data_str:
                        header, data_str = data_str.split(";base64,", 1)
                        mime = header.split(":", 1)[-1]
                        ext = {"image/jpeg": "jpg", "image/webp": "webp", "image/png": "png"}.get(mime, "png")
                    data = base64.b64decode(data_str)
                elif isinstance(payload, (bytes, bytearray)):
                    data = bytes(payload)
                else:
                    continue
                out = assets_dir / f"{stem}.{ext}"
                out.write_bytes(data)
                return relative_to_or_name(out, assets_dir.parent)
            except Exception:
                continue
        return None

    @staticmethod
    def _horizontal_overlap(a, b) -> float:
        if a is None or b is None:
            return 0.0
        inter = max(0.0, min(a.x_max, b.x_max) - max(a.x_min, b.x_min))
        denom = max(1e-9, min(a.x_max - a.x_min, b.x_max - b.x_min))
        return inter / denom

    @classmethod
    def _associate_captions(cls, page: Page) -> None:
        captions = [t for t in page.text_blocks if t.block_type == "caption" and t.bbox is not None]
        targets: list[Any] = [*page.tables, *page.images, *page.diagrams]
        for target in targets:
            if target.bbox is None or getattr(target, "caption", None) is not None:
                continue
            candidates = []
            for cap in captions:
                dy = min(abs(cap.bbox.y_min - target.bbox.y_max), abs(target.bbox.y_min - cap.bbox.y_max))
                overlap = cls._horizontal_overlap(target.bbox, cap.bbox)
                if dy <= 0.08 and overlap >= 0.2:
                    candidates.append((dy, -overlap, cap))
            if candidates:
                cap = sorted(candidates, key=lambda x: (x[0], x[1]))[0][2]
                target.caption = Caption(text=cap.raw_text, bbox=cap.bbox)

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
            raise ToolOutputParseError("Marker JSON output is missing")
        raw = read_json(raw_result.primary_artifact)
        pages_out: list[Page] = []

        for page_idx, page_raw in enumerate(raw.get("children", []) or []):
            pn = page_idx + 1
            pb = page_raw.get("bbox") or [0, 0, 1, 1]
            width = max(1.0, float(pb[2]) - float(pb[0]))
            height = max(1.0, float(pb[3]) - float(pb[1]))
            page = Page(page_number=pn, width=width, height=height)
            order = 0

            for leaf in self._leaf_blocks(page_raw):
                typ = self._type_name(leaf.get("block_type"))
                if typ == "Page":
                    continue
                raw_bbox = leaf.get("bbox")
                bbox = normalize_bbox(raw_bbox, width, height) if raw_bbox else None
                eid = str(leaf.get("id") or f"p{pn}_{typ}_{order}")
                html = leaf.get("html") or ""

                if typ in {"Equation", "TextInlineMath"}:
                    value = self._math_from_html(html)
                    page.formulas.append(
                        Formula(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            raw_text=html_to_text(html),
                            latex=value or None,
                            plain_text=html_to_text(html),
                            formula_type="inline" if typ == "TextInlineMath" else "display",
                            order_index=order,
                            provenance={"marker_block_type": typ},
                        )
                    )
                    page.reading_order.append(eid)
                    order += 1
                    continue

                if typ == "Table":
                    rows, cols, cells = parse_html_table(html)
                    page.tables.append(
                        Table(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            rows=rows,
                            columns=cols,
                            cells=cells,
                            html=html,
                            order_index=order,
                            provenance={"marker_block_type": typ},
                        )
                    )
                    page.reading_order.append(eid)
                    order += 1
                    continue

                if typ in {"Picture", "Figure", "Diagram"}:
                    asset = self._save_images(leaf.get("images"), assets_dir, f"p{pn}_{order:04d}")
                    extracted_text = html_to_text(html)
                    if typ == "Diagram":
                        page.diagrams.append(
                            DiagramObject(
                                element_id=eid,
                                page_number=pn,
                                bbox=bbox,
                                asset_path=asset,
                                text_elements=extracted_text.split() if extracted_text else [],
                                order_index=order,
                                provenance={"marker_block_type": typ, "html": html},
                            )
                        )
                    else:
                        page.images.append(
                            ImageObject(
                                element_id=eid,
                                page_number=pn,
                                bbox=bbox,
                                asset_path=asset,
                                extraction_success=asset is not None,
                                order_index=order,
                                provenance={"marker_block_type": typ, "html": html},
                            )
                        )
                    page.reading_order.append(eid)
                    order += 1
                    continue

                text = html_to_text(html)
                if not text:
                    continue
                block_type = "paragraph"
                if typ == "SectionHeader":
                    block_type = "heading"
                elif typ == "ListItem":
                    block_type = "list_item"
                elif typ == "Caption":
                    block_type = "caption"
                elif typ == "PageHeader":
                    block_type = "header"
                elif typ == "PageFooter":
                    block_type = "footer"
                page.text_blocks.append(
                    TextBlock(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        raw_text=text,
                        block_type=block_type,
                        order_index=order,
                        provenance={"marker_block_type": typ},
                    )
                )
                page.reading_order.append(eid)
                order += 1

            self._associate_captions(page)
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
                    "formulas": True,
                    "chemical_objects": False,
                    "images": True,
                    "diagrams": True,
                    "reading_order": "Marker JSON tree child order",
                    "ocr": True,
                    "llm_augmentation": False,
                },
                "marker_metadata": raw.get("metadata", {}),
            },
        )
