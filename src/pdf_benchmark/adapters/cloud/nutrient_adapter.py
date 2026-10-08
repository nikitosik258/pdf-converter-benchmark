from __future__ import annotations

import asyncio
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import ToolExecutionError, ToolOutputParseError
from pdf_benchmark.adapters.cloud.base import BaseCloudAdapter
from pdf_benchmark.adapters.cloud.helpers import clamp, pdf_page_count, pdf_page_sizes
from pdf_benchmark.models import (
    BBox,
    Caption,
    DiagramObject,
    Formula,
    ImageObject,
    Page,
    RawToolResult,
    StandardizedDocument,
    Table,
    TableCell,
    TextBlock,
)
from pdf_benchmark.utils.io import ensure_dir, read_json, write_json


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "as_dict"):
        return _jsonable(value.as_dict())
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    return str(value)


def _page_number(element: dict[str, Any]) -> int:
    page = element.get("page") or {}
    if isinstance(page, dict):
        if page.get("pageNumber") is not None:
            return max(1, int(page["pageNumber"]))
        if page.get("page_number") is not None:
            return max(1, int(page["page_number"]))
        if page.get("pageIndex") is not None:
            return int(page["pageIndex"]) + 1
        if page.get("page_index") is not None:
            return int(page["page_index"]) + 1
    for key in ("pageNumber", "page_number"):
        if element.get(key) is not None:
            return max(1, int(element[key]))
    for key in ("pageIndex", "page_index"):
        if element.get(key) is not None:
            return int(element[key]) + 1
    return 1


def _coordinate_page_size(
    element: dict[str, Any], source_size: tuple[float, float],
) -> tuple[float, float]:
    """Use the provider's canvas for bounds, retaining PDF page metadata.

    Legacy responses without canvas dimensions use PDF units. When dimensions
    are supplied, both must be valid: mixing provider pixels with PDF points
    silently moves/clips objects and corrupts their benchmark matches.
    """
    page = element.get("page") or {}
    if not isinstance(page, dict) or (page.get("width") is None and page.get("height") is None):
        return source_size
    try:
        width, height = float(page["width"]), float(page["height"])
        if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
            raise ValueError("Canvas dimensions must be finite and positive")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ToolOutputParseError(
            f"Invalid Nutrient coordinate canvas for element {element.get('id', '<unknown>')!r}"
        ) from exc
    return width, height


def _bounds_to_bbox(bounds: Any, width: float, height: float) -> BBox | None:
    if not bounds:
        return None

    if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
        x, y, w, h = [float(v or 0) for v in bounds[:4]]
        vals = (x, y, x + w, y + h)
    elif isinstance(bounds, dict):
        if all(k in bounds for k in ("x", "y", "width", "height")):
            x = float(bounds.get("x") or 0)
            y = float(bounds.get("y") or 0)
            w = float(bounds.get("width") or 0)
            h = float(bounds.get("height") or 0)
            vals = (x, y, x + w, y + h)
        elif any(k in bounds for k in ("xMin", "xmin", "left")):
            x0 = float(bounds.get("xMin", bounds.get("xmin", bounds.get("left", 0))) or 0)
            y0 = float(bounds.get("yMin", bounds.get("ymin", bounds.get("top", 0))) or 0)
            x1 = float(bounds.get("xMax", bounds.get("xmax", bounds.get("right", x0))) or x0)
            y1 = float(bounds.get("yMax", bounds.get("ymax", bounds.get("bottom", y0))) or y0)
            vals = (x0, y0, x1, y1)
        else:
            return None
    else:
        return None

    x0, y0, x1, y1 = vals
    # Some responses may already use normalized page coordinates.
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.000001:
        return BBox(
            x_min=clamp(x0), y_min=clamp(y0),
            x_max=clamp(x1), y_max=clamp(y1),
        )
    if width <= 0 or height <= 0:
        return None
    return BBox(
        x_min=clamp(x0 / width), y_min=clamp(y0 / height),
        x_max=clamp(x1 / width), y_max=clamp(y1 / height),
    )


def _text_of(element: dict[str, Any]) -> str:
    for key in ("text", "content", "value", "markdown"):
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _confidence(value: Any) -> float | None:
    try:
        if value is None:
            return None
        v = float(value)
        if 0 <= v <= 1:
            return v
    except Exception:
        pass
    return None


def _caption(value: Any, width: float, height: float) -> Caption | None:
    if isinstance(value, str) and value.strip():
        return Caption(text=value)
    if isinstance(value, dict):
        text = _text_of(value)
        if text:
            return Caption(
                text=text,
                bbox=_bounds_to_bbox(value.get("bounds") or value.get("bbox"), width, height),
            )
    return None


class NutrientDataExtractionAdapter(BaseCloudAdapter):
    """Nutrient Data Extraction API adapter.

    Google Cloud Vision is intentionally replaced by Nutrient because Nutrient
    offers a standing no-card free tier. The benchmark uses `understand` mode
    with spatial JSON because the corpus contains tables, multi-column text,
    formulas, images and diagrams.
    """

    tool_name = "nutrient"
    distribution_name = "nutrient-dws"
    pinned_version = "3.1.0"
    required_env = ("NUTRIENT_DWS_EXTRACTION_API_KEY",)

    def model_versions(self) -> dict[str, str]:
        return {
            "product": "Nutrient Data Extraction API",
            "endpoint": "/extraction/parse",
            "mode": str(self.config.get("mode", "understand")),
            "output_format": "spatial",
        }

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        if self.mock_mode:
            return self.load_mock_json(raw_dir)

        self.require_credentials()
        ensure_dir(raw_dir)
        try:
            from nutrient_dws import NutrientClient
        except Exception as exc:
            raise ToolExecutionError(
                "nutrient-dws is not installed in .venv-cloud. "
                "Install the patched cloud requirements."
            ) from exc

        key = os.environ["NUTRIENT_DWS_EXTRACTION_API_KEY"]
        mode = str(self.config.get("mode", "understand"))
        if mode not in {"structure", "understand", "agentic"}:
            raise ToolExecutionError(
                f"Unsupported Nutrient spatial mode {mode!r}; use structure, understand or agentic."
            )

        async def _call() -> Any:
            # Data Extraction currently has a separate static key. Supplying it
            # as both values keeps the official client constructor satisfied;
            # parse() uses extract_api_key for /extraction/parse.
            client = NutrientClient(api_key=key, extract_api_key=key)
            try:
                return await client.parse(
                    str(pdf_path),
                    mode=mode,
                    output_format="spatial",
                )
            finally:
                close = getattr(client, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result

        started = time.monotonic()
        try:
            # Do not auto-retry the upload. A transport failure after server
            # acceptance could otherwise consume credits twice.
            response = asyncio.run(_call())
        except Exception as exc:
            status = (
                getattr(exc, "status_code", None)
                or getattr(exc, "status", None)
                or getattr(getattr(exc, "response", None), "status_code", None)
            )
            if status in (401, 403):
                raise ToolExecutionError(
                    "Nutrient authentication failed. Use a Data Extraction API key "
                    "in NUTRIENT_DWS_EXTRACTION_API_KEY, not a Processor-only key."
                ) from exc
            if status in (402, 429):
                raise ToolExecutionError(
                    "Nutrient free quota/rate limit blocked the request. "
                    "Do not enable pay-as-you-go for this benchmark."
                ) from exc
            raise ToolExecutionError(f"Nutrient Data Extraction request failed: {exc}") from exc
        latency = time.monotonic() - started

        payload = _jsonable(response)
        if not isinstance(payload, dict):
            raise ToolOutputParseError("Nutrient response is not a JSON object")
        output = payload.get("output") or {}
        elements = output.get("elements") or []
        if not isinstance(elements, list) or not elements:
            raise ToolOutputParseError(
                "Nutrient spatial response contains no output.elements"
            )

        target = raw_dir / "response.json"
        write_json(target, payload)

        pages = pdf_page_count(pdf_path)
        credits_per_page = {
            "structure": 1.5,
            "understand": 9.0,
            "agentic": 18.0,
        }[mode]
        estimated_credits = pages * credits_per_page
        free_credits = float(self.config.get("free_credits_per_month", 5000))
        assume_free = bool(self.config.get("assume_free_quota_available", True))

        usage = payload.get("usage") or {}
        extraction_usage = usage.get("data_extraction_credits") or usage.get("dataExtractionCredits") or {}

        return RawToolResult(
            primary_artifact=str(target),
            artifacts=[str(target)],
            metadata={
                "mock": False,
                "network_calls": 1,
                "pages": pages,
                "mode": mode,
                "credits_per_page": credits_per_page,
                "credits_estimate": estimated_credits,
                "provider_usage": extraction_usage,
                "latency_seconds": latency,
                "processing_seconds": latency,
                "estimated_cost_usd": 0.0 if assume_free and estimated_credits <= free_credits else None,
                "actual_cost_usd": None,
                "pricing_basis": (
                    "Nutrient Data Extraction free plan: 5,000 credits/month; "
                    f"Parse {mode} mode uses {credits_per_page:g} credits/page."
                ),
            },
        )

    def standardize(
        self,
        raw_result,
        raw_dir,
        assets_dir,
        *,
        document_id,
        pdf_path,
    ):
        payload = read_json(
            raw_dir / "mock_response.json"
            if raw_result.metadata.get("mock")
            else Path(raw_result.primary_artifact or "")
        )
        output = payload.get("output") or {}
        elements = output.get("elements") or []
        if not isinstance(elements, list) or not elements:
            raise ToolOutputParseError("Nutrient response contained no spatial elements")

        source_sizes = pdf_page_sizes(pdf_path)
        pages: dict[int, Page] = {
            i + 1: Page(page_number=i + 1, width=w, height=h)
            for i, (w, h) in enumerate(source_sizes)
        }

        order = 0
        for native in elements:
            if not isinstance(native, dict):
                continue
            page_no = _page_number(native)
            if page_no not in pages:
                # Defensive fallback for a provider page index beyond source metadata.
                pages[page_no] = Page(page_number=page_no, width=1.0, height=1.0)
            page = pages[page_no]
            width, height = _coordinate_page_size(native, (page.width, page.height))
            etype = str(native.get("type") or native.get("elementType") or "").strip()
            etype_lower = etype.lower().replace("_", "").replace("-", "")
            eid = str(native.get("id") or f"nutrient_p{page_no}_{order:06d}")
            bbox = _bounds_to_bbox(native.get("bounds") or native.get("bbox"), width, height)
            conf = _confidence(native.get("confidence"))

            if etype_lower == "table":
                cells: list[TableCell] = []
                for cell in native.get("cells") or []:
                    if not isinstance(cell, dict):
                        continue
                    cells.append(
                        TableCell(
                            row_index=max(0, int(cell.get("row", cell.get("rowIndex", 0)) or 0)),
                            column_index=max(0, int(cell.get("column", cell.get("columnIndex", 0)) or 0)),
                            row_span=max(1, int(cell.get("rowSpan", 1) or 1)),
                            column_span=max(1, int(cell.get("columnSpan", 1) or 1)),
                            text=str(cell.get("text") or cell.get("content") or ""),
                            is_header=bool(cell.get("isHeader", cell.get("header", False))),
                            bbox=_bounds_to_bbox(cell.get("bounds") or cell.get("bbox"), width, height),
                        )
                    )
                rows = int(native.get("rowCount") or native.get("rows") or 0)
                cols = int(native.get("columnCount") or native.get("columns") or 0)
                if rows <= 0 and cells:
                    rows = max(c.row_index + c.row_span for c in cells)
                if cols <= 0 and cells:
                    cols = max(c.column_index + c.column_span for c in cells)
                page.tables.append(
                    Table(
                        element_id=eid,
                        page_number=page_no,
                        bbox=bbox,
                        rows=max(0, rows),
                        columns=max(0, cols),
                        cells=cells,
                        caption=_caption(native.get("caption"), width, height),
                        confidence=conf,
                        order_index=order,
                        provenance={"source": "output.elements", "native_type": etype},
                    )
                )

            elif etype_lower in {"formula", "equation", "mathformula"}:
                latex = native.get("latex") or native.get("LaTeX") or native.get("formula")
                raw_text = _text_of(native) or None
                page.formulas.append(
                    Formula(
                        element_id=eid,
                        page_number=page_no,
                        bbox=bbox,
                        raw_text=raw_text,
                        latex=str(latex) if latex is not None else None,
                        plain_text=raw_text,
                        confidence=conf,
                        order_index=order,
                        provenance={"source": "output.elements", "native_type": etype},
                    )
                )

            elif etype_lower in {"chart", "diagram", "scheme", "flowchart"}:
                text_elements = native.get("textElements") or native.get("text_elements") or []
                if isinstance(text_elements, str):
                    text_elements = [text_elements]
                key_elements = native.get("keyElements") or native.get("key_elements") or []
                if isinstance(key_elements, str):
                    key_elements = [key_elements]
                page.diagrams.append(
                    DiagramObject(
                        element_id=eid,
                        page_number=page_no,
                        bbox=bbox,
                        diagram_type="chart" if etype_lower == "chart" else "unknown",
                        caption=_caption(native.get("caption") or native.get("description"), width, height),
                        text_elements=[str(x) for x in text_elements],
                        key_elements=[str(x) for x in key_elements],
                        confidence=conf,
                        order_index=order,
                        provenance={"source": "output.elements", "native_type": etype},
                    )
                )

            elif etype_lower in {"picture", "image", "figure"}:
                semantic = str(
                    native.get("semanticType")
                    or native.get("semantic_type")
                    or native.get("classification")
                    or ""
                ).lower()
                if any(token in semantic for token in ("chart", "diagram", "scheme", "flow")):
                    page.diagrams.append(
                        DiagramObject(
                            element_id=eid,
                            page_number=page_no,
                            bbox=bbox,
                            diagram_type="chart" if "chart" in semantic else "unknown",
                            caption=_caption(native.get("caption") or native.get("description"), width, height),
                            confidence=conf,
                            order_index=order,
                            provenance={"source": "output.elements", "native_type": etype, "semantic_type": semantic},
                        )
                    )
                else:
                    page.images.append(
                        ImageObject(
                            element_id=eid,
                            page_number=page_no,
                            bbox=bbox,
                            caption=_caption(native.get("caption") or native.get("description"), width, height),
                            extraction_success=False,
                            confidence=conf,
                            order_index=order,
                            provenance={"source": "output.elements", "native_type": etype, "semantic_type": semantic},
                        )
                    )

            else:
                text = _text_of(native)
                if not text and etype_lower == "keyvalueregion":
                    parts: list[str] = []
                    for pair in native.get("pairs") or []:
                        if not isinstance(pair, dict):
                            continue
                        key = pair.get("key") or {}
                        value = pair.get("value") or {}
                        k = _text_of(key) if isinstance(key, dict) else str(key or "")
                        v = _text_of(value) if isinstance(value, dict) else str(value or "")
                        if k or v:
                            parts.append(f"{k}: {v}".strip())
                    text = "\n".join(parts)
                if text:
                    block_type = "paragraph"
                    if etype_lower in {"heading", "title", "sectionheading"}:
                        block_type = "heading"
                    elif etype_lower in {"listitem", "list"}:
                        block_type = "list_item"
                    elif etype_lower == "caption":
                        block_type = "caption"
                    elif etype_lower == "header":
                        block_type = "header"
                    elif etype_lower == "footer":
                        block_type = "footer"
                    page.text_blocks.append(
                        TextBlock(
                            element_id=eid,
                            page_number=page_no,
                            bbox=bbox,
                            raw_text=text,
                            block_type=block_type,
                            confidence=conf,
                            order_index=order,
                            provenance={"source": "output.elements", "native_type": etype},
                        )
                    )

            page.reading_order.append(eid)
            order += 1

        if not any(
            p.text_blocks or p.tables or p.formulas or p.images or p.diagrams
            for p in pages.values()
        ):
            raise ToolOutputParseError("Nutrient response produced no standardized objects")

        return StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path),
            tool=self.tool_metadata(),
            pages=[pages[k] for k in sorted(pages)],
            metadata={"cloud_execution": raw_result.metadata},
        )
