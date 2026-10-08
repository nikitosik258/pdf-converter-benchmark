from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import BaseLocalAdapter, ToolExecutionError, ToolOutputParseError
from pdf_benchmark.models import Caption, DiagramObject, Formula, ImageObject, Page, RawToolResult, StandardizedDocument, Table, TextBlock
from pdf_benchmark.utils.geometry import normalize_1000_bbox, normalize_bbox
from pdf_benchmark.utils.html_tables import parse_html_table
from pdf_benchmark.utils.io import read_json, relative_to_or_name
from pdf_benchmark.utils.markdown_tables import parse_markdown_table
from pdf_benchmark.utils.versions import installed_versions
from pdf_benchmark.utils.text import collapse_ws, html_to_text, strip_math_wrappers


class MinerUAdapter(BaseLocalAdapter):
    tool_name = "mineru"
    distribution_name = "mineru"
    pinned_version = "4.0.0"

    DEFAULT_CONFIG = {
        "tier": "standard",
        "ocr_mode": "auto",
        "page_range": "all",
        "resource_sample_interval": 0.2,
    }

    def model_versions(self) -> dict[str, str]:
        return installed_versions(['mineru', 'docvortex', 'onnxruntime', 'llama-cpp-python', 'torch'])

    def __init__(self, config: dict[str, Any] | None = None, logger=None):
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(config or {})
        # The benchmark fixes Standard, not Advanced, to avoid per-document tuning.
        merged["tier"] = "standard"
        super().__init__(merged, logger=logger)

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        try:
            from mineru.parser import parse
            from mineru.parser.writer import FileBasedDataWriter
        except Exception as exc:  # pragma: no cover
            raise ToolExecutionError("MinerU 4 is not installed") from exc

        saved = raw_dir / "saved"
        saved.mkdir(parents=True, exist_ok=True)
        try:
            result = parse(
                pdf_path,
                tier=str(self.config["tier"]),
                ocr_mode=str(self.config["ocr_mode"]),
                page_range=str(self.config["page_range"]),
            )
            result.save(FileBasedDataWriter(str(saved)))
        except Exception as exc:
            raise ToolExecutionError(f"MinerU conversion failed on {pdf_path.name}: {exc}") from exc

        structured = saved / "structured_content.json"
        if not structured.exists():
            # Defensive fallback: the public ParseResult API still exposes structured_content().
            try:
                import json
                structured.write_text(json.dumps(result.structured_content(), ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                raise ToolExecutionError("MinerU did not produce structured_content.json") from exc

        artifacts = [p for p in saved.rglob("*") if p.is_file()]
        return RawToolResult(
            primary_artifact=str(structured),
            artifacts=[relative_to_or_name(p, raw_dir.parent) for p in artifacts],
            metadata={"saved_dir": str(saved), "page_count": len(getattr(result, "pages", []) or [])},
        )

    @staticmethod
    def _flatten_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return collapse_ws(value)
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return collapse_ws(" ".join(MinerUAdapter._flatten_text(v) for v in value))
        if isinstance(value, dict):
            preferred = (
                "text", "content", "paragraph_content", "title_content", "math_content",
                "caption", "image_caption", "table_caption", "chart_caption", "list_items",
            )
            parts = []
            for key in preferred:
                if key in value:
                    parts.append(MinerUAdapter._flatten_text(value[key]))
            if parts:
                return collapse_ws(" ".join(parts))
            return collapse_ws(" ".join(MinerUAdapter._flatten_text(v) for v in value.values()))
        return collapse_ws(str(value))

    @staticmethod
    def _bbox(block: dict[str, Any]):
        raw = block.get("bbox")
        if raw is None and isinstance(block.get("location"), dict):
            raw = block["location"].get("bbox")
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            return None
        vals = [float(v) for v in raw[:4]]
        if max(abs(v) for v in vals) <= 1.5:
            return normalize_bbox(vals, 1.0, 1.0)
        return normalize_1000_bbox(vals)

    @staticmethod
    def _block_type(block: dict[str, Any]) -> str:
        return str(block.get("type") or block.get("block_type") or "").lower().replace("-", "_")

    @staticmethod
    def _content_dict(block: dict[str, Any]) -> dict[str, Any]:
        c = block.get("content")
        return c if isinstance(c, dict) else {}

    @classmethod
    def _formula_latex(cls, block: dict[str, Any]) -> str:
        c = cls._content_dict(block)
        for key in ("latex", "math_content", "equation_content"):
            if c.get(key):
                return strip_math_wrappers(str(c[key]))
        if block.get("text_format") == "latex" and block.get("text"):
            return strip_math_wrappers(str(block["text"]))
        if block.get("text") and "equation" in cls._block_type(block):
            return strip_math_wrappers(str(block["text"]))
        return ""

    @classmethod
    def _table_html(cls, block: dict[str, Any]) -> str:
        c = cls._content_dict(block)
        for key in ("html", "table_body"):
            if c.get(key):
                return str(c[key])
        for key in ("table_body", "html"):
            if block.get(key):
                return str(block[key])
        content = block.get("content")
        if isinstance(content, str) and re.search(r"<table\b", content, re.IGNORECASE):
            return content
        return ""

    @classmethod
    def _caption_text(cls, block: dict[str, Any], kind: str) -> str:
        c = cls._content_dict(block)
        captions = c.get("captions") or block.get("captions") or []
        if not isinstance(captions, list):
            captions = [captions]
        # Native v4 captions contain content and geometry. Read only text,
        # preserving caption order; coordinates must never become caption text.
        parts = [
            cls._flatten_text(value.get("content") or value.get("text") or "")
            if isinstance(value, dict) else value if isinstance(value, str) else ""
            for value in captions
        ]
        text = collapse_ws(" ".join(parts))
        if text:
            return text  # Legacy aliases may contain the same caption.
        candidates = [
            c.get("caption"),
            c.get(f"{kind}_caption"),
            block.get("caption"),
            block.get(f"{kind}_caption"),
        ]
        return collapse_ws(" ".join(cls._flatten_text(x) for x in candidates if x))

    @classmethod
    def _asset_source(cls, block: dict[str, Any]) -> str | None:
        c = cls._content_dict(block)
        for source in (c.get("source"), c.get("image_source"), block.get("source"), block.get("image_source")):
            if isinstance(source, str) and source.strip():
                return source.strip()
            if isinstance(source, dict) and source.get("path"):
                return str(source["path"])
        for key in ("img_path", "image_path"):
            if block.get(key):
                return str(block[key])
            if c.get(key):
                return str(c[key])
        return None

    @staticmethod
    def _copy_asset(source: str | None, saved_dir: Path, assets_dir: Path, stem: str) -> str | None:
        if not source:
            return None
        src = Path(source)
        candidates = [
            saved_dir / src,
            saved_dir / "images" / src.name,
            saved_dir / src.name,
        ]
        real = next((p for p in candidates if p.exists() and p.is_file()), None)
        if real is None:
            return None
        suffix = real.suffix or ".png"
        out = assets_dir / f"{stem}{suffix}"
        shutil.copy2(real, out)
        return relative_to_or_name(out, assets_dir.parent)

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
            raise ToolOutputParseError("MinerU structured_content.json is missing")
        raw = read_json(raw_result.primary_artifact)
        saved_dir = Path(raw_result.metadata.get("saved_dir") or (raw_dir / "saved"))
        pages_out: list[Page] = []

        for page_raw in raw.get("pages", []) or []:
            pn = int(page_raw.get("page_idx", len(pages_out))) + 1
            page = Page(page_number=pn, width=1.0, height=1.0)
            for order, block in enumerate(page_raw.get("blocks", []) or []):
                typ = self._block_type(block)
                bbox = self._bbox(block)
                eid = str(block.get("id") or block.get("block_id") or f"p{pn}_{typ}_{order}")

                if "equation" in typ or typ in {"formula", "interline_equation"}:
                    latex = self._formula_latex(block)
                    page.formulas.append(
                        Formula(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            raw_text=self._flatten_text(block),
                            latex=latex or None,
                            plain_text=self._flatten_text(block),
                            formula_type="display",
                            order_index=order,
                            provenance={"mineru_type": typ},
                        )
                    )
                    page.reading_order.append(eid)
                    continue

                if typ == "table" or "table_" in typ:
                    html = self._table_html(block)
                    provenance = {"mineru_type": typ}
                    if html:
                        rows, cols, cells = parse_html_table(html)
                    else:
                        content = block.get("content")
                        markdown = content if isinstance(content, str) else ""
                        rows, cols, cells = parse_markdown_table(markdown)
                        if markdown:
                            provenance["markdown"] = markdown
                    cap = self._caption_text(block, "table")
                    page.tables.append(
                        Table(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            rows=rows,
                            columns=cols,
                            cells=cells,
                            html=html or None,
                            caption=Caption(text=cap) if cap else None,
                            order_index=order,
                            provenance=provenance,
                        )
                    )
                    page.reading_order.append(eid)
                    continue

                if typ == "chart" or "chart" in typ:
                    src = self._asset_source(block)
                    asset = self._copy_asset(src, saved_dir, assets_dir, f"p{pn}_chart_{order:04d}")
                    cap = self._caption_text(block, "chart")
                    c = self._content_dict(block)
                    chart_content = c.get("content") or block.get("content")
                    text = html_to_text(chart_content) if isinstance(chart_content, str) else self._flatten_text(chart_content)
                    page.diagrams.append(
                        DiagramObject(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            diagram_type="chart",
                            asset_path=asset,
                            caption=Caption(text=cap) if cap else None,
                            text_elements=text.split() if text else [],
                            order_index=order,
                            provenance={"mineru_type": typ},
                        )
                    )
                    page.reading_order.append(eid)
                    continue

                if typ == "image" or typ.startswith("image_"):
                    src = self._asset_source(block)
                    asset = self._copy_asset(src, saved_dir, assets_dir, f"p{pn}_image_{order:04d}")
                    cap = self._caption_text(block, "image")
                    page.images.append(
                        ImageObject(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            asset_path=asset,
                            caption=Caption(text=cap) if cap else None,
                            extraction_success=asset is not None,
                            order_index=order,
                            provenance={"mineru_type": typ},
                        )
                    )
                    page.reading_order.append(eid)
                    continue

                text = self._flatten_text(block.get("content") if "content" in block else block.get("text"))
                if not text:
                    continue
                block_type = "paragraph"
                if "title" in typ:
                    block_type = "heading"
                elif "list" in typ or "index" in typ:
                    block_type = "list_item"
                elif "caption" in typ:
                    block_type = "caption"
                elif "header" in typ:
                    block_type = "header"
                elif "footer" in typ or "page_number" in typ:
                    block_type = "footer"
                page.text_blocks.append(
                    TextBlock(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        raw_text=text,
                        block_type=block_type,
                        order_index=order,
                        provenance={"mineru_type": typ},
                    )
                )
                page.reading_order.append(eid)

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
                    "diagrams": "native chart blocks",
                    "reading_order": "Structured Content block order",
                    "ocr": True,
                },
                "mineru_metadata": raw.get("metadata", {}),
                "mineru_extensions": raw.get("extensions", {}),
            },
        )
