\
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

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
        try:
            return _jsonable(value.model_dump(mode="json"))
        except TypeError:
            return _jsonable(value.model_dump())
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    if hasattr(value, "__dict__"):
        return _jsonable({k: v for k, v in vars(value).items() if not k.startswith("_")})
    return str(value)


def _first_str(obj: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _page_number(obj: dict[str, Any], fallback: int) -> int:
    for key in ("page_number", "pageNumber", "page"):
        value = obj.get(key)
        if isinstance(value, dict):
            for nested in ("page_number", "pageNumber", "number", "index"):
                if value.get(nested) is not None:
                    n = int(value[nested])
                    return n + 1 if nested == "index" else max(1, n)
        elif value is not None and not isinstance(value, (list, tuple)):
            try:
                return max(1, int(value))
            except Exception:
                pass
    for key in ("page_index", "pageIndex"):
        if obj.get(key) is not None:
            try:
                return int(obj[key]) + 1
            except Exception:
                pass
    return fallback


def _bbox_from_any(value: Any, width: float, height: float) -> BBox | None:
    if value is None:
        return None
    if isinstance(value, dict):
        # Some item payloads nest coordinates under a second bbox/bounds key.
        for nested in ("bbox", "b_box", "bounds", "box"):
            if nested in value and isinstance(value[nested], (dict, list, tuple)):
                nested_bbox = _bbox_from_any(value[nested], width, height)
                if nested_bbox is not None:
                    return nested_bbox
        if all(k in value for k in ("x", "y")):
            x0 = float(value.get("x") or 0)
            y0 = float(value.get("y") or 0)
            w = value.get("w", value.get("width"))
            h = value.get("h", value.get("height"))
            if w is not None and h is not None:
                x1 = x0 + float(w or 0)
                y1 = y0 + float(h or 0)
            else:
                x1 = float(value.get("x2", value.get("x_max", value.get("xMax", x0))) or x0)
                y1 = float(value.get("y2", value.get("y_max", value.get("yMax", y0))) or y0)
        else:
            x0 = float(value.get("x0", value.get("x_min", value.get("xMin", value.get("left", 0)))) or 0)
            y0 = float(value.get("y0", value.get("y_min", value.get("yMin", value.get("top", 0)))) or 0)
            x1 = float(value.get("x1", value.get("x_max", value.get("xMax", value.get("right", x0)))) or x0)
            y1 = float(value.get("y1", value.get("y_max", value.get("yMax", value.get("bottom", y0)))) or y0)
    elif isinstance(value, (list, tuple)):
        if not value:
            return None

        # Legacy/compact form: [x0, y0, x1, y1].
        if (
            len(value) >= 4
            and all(not isinstance(v, (dict, list, tuple)) for v in value[:4])
        ):
            try:
                x0, y0, x1, y1 = (float(v or 0) for v in value[:4])
            except (TypeError, ValueError):
                return None
        else:
            # Current LlamaParse item payloads may wrap one or more rectangular
            # regions as a list of dictionaries, e.g.
            # [{"x": 70.1, "y": 136.1, "w": 451.8, "h": 32.96, ...}].
            #
            # Recursing also handles polygon-like lists of {x, y} points:
            # every point becomes a zero-area box and the union below becomes
            # the polygon's enclosing rectangle.
            boxes = [
                b
                for part in value
                if (b := _bbox_from_any(part, width, height)) is not None
            ]
            if not boxes:
                return None
            return BBox(
                x_min=min(b.x_min for b in boxes),
                y_min=min(b.y_min for b in boxes),
                x_max=max(b.x_max for b in boxes),
                y_max=max(b.y_max for b in boxes),
            )
    else:
        return None

    values = (x0, y0, x1, y1)
    if max(abs(v) for v in values) <= 1.000001:
        return BBox(x_min=clamp(x0), y_min=clamp(y0), x_max=clamp(x1), y_max=clamp(y1))
    if width <= 0 or height <= 0:
        return None
    return BBox(
        x_min=clamp(x0 / width),
        y_min=clamp(y0 / height),
        x_max=clamp(x1 / width),
        y_max=clamp(y1 / height),
    )


def _item_bbox(item: dict[str, Any], width: float, height: float) -> BBox | None:
    for key in ("b_box", "bbox", "bounding_box", "boundingBox", "bounds", "box"):
        if key in item:
            bbox = _bbox_from_any(item.get(key), width, height)
            if bbox is not None:
                return bbox
    return None


def _item_type(item: dict[str, Any]) -> str:
    value = _first_str(item, ("type", "item_type", "itemType", "kind", "category", "label"))
    return value.lower().replace("-", "_").replace(" ", "_")


def _item_text(item: dict[str, Any]) -> str:
    return _first_str(item, ("text", "content", "value", "md", "markdown", "raw_text"))


def _caption(item: dict[str, Any], width: float, height: float) -> Caption | None:
    value = item.get("caption")
    if isinstance(value, str) and value.strip():
        return Caption(text=value.strip())
    if isinstance(value, dict):
        text = _item_text(value)
        if text:
            return Caption(text=text, bbox=_item_bbox(value, width, height))
    alt = _first_str(item, ("alt", "alt_text", "description", "title"))
    return Caption(text=alt) if alt else None


def _parse_markdown_table(markdown: str) -> tuple[int, int, list[TableCell]]:
    lines = [line.strip() for line in markdown.splitlines() if "|" in line]
    if len(lines) < 2:
        return 0, 0, []

    parsed: list[list[str]] = []
    for line in lines:
        content = line.strip().strip("|")
        cells = [c.strip() for c in content.split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells):
            continue
        parsed.append(cells)
    if not parsed:
        return 0, 0, []
    cols = max(len(r) for r in parsed)
    cells_out: list[TableCell] = []
    for r, row in enumerate(parsed):
        for c in range(cols):
            cells_out.append(TableCell(
                row_index=r,
                column_index=c,
                text=row[c] if c < len(row) else "",
                is_header=(r == 0),
            ))
    return len(parsed), cols, cells_out

def _dimension_count(value: Any) -> int:
    if value is None:
        return 0

    if isinstance(value, (list, tuple, dict)):
        return len(value)

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

def _native_table(item: dict[str, Any], page_no: int, eid: str, bbox: BBox | None, order: int) -> Table:
    native_cells = item.get("cells")
    cells: list[TableCell] = []
    if isinstance(native_cells, list):
        for cell in native_cells:
            if not isinstance(cell, dict):
                continue
            try:
                row = int(cell.get("row_index", cell.get("row", cell.get("rowIndex", 0))) or 0)
                col = int(cell.get("column_index", cell.get("column", cell.get("columnIndex", 0))) or 0)
            except Exception:
                row, col = 0, 0
            cells.append(TableCell(
                row_index=max(0, row),
                column_index=max(0, col),
                row_span=max(1, int(cell.get("row_span", cell.get("rowSpan", 1)) or 1)),
                column_span=max(1, int(cell.get("column_span", cell.get("columnSpan", 1)) or 1)),
                text=_item_text(cell),
                is_header=bool(cell.get("is_header", cell.get("isHeader", cell.get("header", False)))),
            ))

    markdown = _first_str(item, ("md", "markdown", "text", "content"))

    rows = _dimension_count(
    item.get("row_count", item.get("rowCount", item.get("rows")))
    )

    cols = _dimension_count(
        item.get("column_count", item.get("columnCount", item.get("columns")))
    )
    
    if not cells and markdown:
        rows, cols, cells = _parse_markdown_table(markdown)
    if cells:
        if rows <= 0:
            rows = max(c.row_index + c.row_span for c in cells)
        if cols <= 0:
            cols = max(c.column_index + c.column_span for c in cells)
    return Table(
        element_id=eid,
        page_number=page_no,
        bbox=bbox,
        rows=max(0, rows),
        columns=max(0, cols),
        cells=cells,
        caption=None,
        order_index=order,
        provenance={"source": "llamaparse.items", "native_type": _item_type(item)},
    )


def _extract_usage_credits(payload: dict[str, Any]) -> float | None:
    candidates: list[Any] = [
        payload.get("usage"),
        (payload.get("job_metadata") or {}).get("usage")
        if isinstance(payload.get("job_metadata"), dict) else None,
    ]
    for usage in candidates:
        if not isinstance(usage, dict):
            continue
        for key in ("credits", "credits_used", "creditsUsed", "total_credits", "totalCredits"):
            try:
                if usage.get(key) is not None:
                    return float(usage[key])
            except Exception:
                pass
    return None


class LlamaParseAdapter(BaseCloudAdapter):
    """LlamaParse v2 adapter with page/item structured output.

    Azure Document Intelligence remains in the repository and registry; this is
    only the active fifth cloud provider while Azure credentials are unavailable.
    """

    tool_name = "llamaparse"
    distribution_name = "llama-cloud"
    pinned_version = "2.16.0"
    required_env = ("LLAMA_CLOUD_API_KEY",)

    def model_versions(self) -> dict[str, str]:
        return {
            "product": "LlamaParse v2 Parse",
            "tier": str(self.config.get("tier", "agentic")),
            "version": str(self.config.get("version", "2026-09-07")),
        }

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        if self.mock_mode:
            return self.load_mock_json(raw_dir)

        self.require_credentials()
        ensure_dir(raw_dir)
        try:
            from llama_cloud import LlamaCloud
        except Exception as exc:
            raise ToolExecutionError(
                "llama-cloud is not installed in .venv-cloud; install llama-cloud==2.16.0"
            ) from exc

        tier = str(self.config.get("tier", "agentic"))
        version = str(self.config.get("version", "2026-09-07"))
        credits_per_page = float(self.config.get("credits_per_page", 10))
        timeout = float(self.config.get("timeout_seconds", 900))
        polling = float(self.config.get("polling_interval_seconds", 1.0))
        max_polling = float(self.config.get("max_poll_interval_seconds", 5.0))

        client = LlamaCloud(api_key=os.environ["LLAMA_CLOUD_API_KEY"])
        started = time.monotonic()
        try:
            with pdf_path.open("rb") as fh:
                # One request path with no generic retry: an ambiguous transport
                # failure must never silently consume parse credits twice.
                response = client.parsing.parse(
                    upload_file=fh,
                    tier=tier,
                    version=version,
                    expand=["items", "markdown", "job_metadata"],
                    output_options={
                        # Item-level layout boxes are returned even when this is empty.
                        # Avoid a large sidecar that we do not use for scoring.
                        "granular_bboxes": [],
                        "images_to_save": ["embedded", "layout"]
                        if bool(self.config.get("save_layout_images", True))
                        else [],
                    },
                    polling_interval=polling,
                    max_interval=max_polling,
                    timeout=timeout,
                    backoff="linear",
                    verbose=False,
                )
        except Exception as exc:
            status = (
                getattr(exc, "status_code", None)
                or getattr(exc, "status", None)
                or getattr(getattr(exc, "response", None), "status_code", None)
            )
            if status in (401, 403):
                raise ToolExecutionError(
                    "LlamaParse authentication/authorization failed. Check LLAMA_CLOUD_API_KEY."
                ) from exc
            if status in (402, 429):
                raise ToolExecutionError(
                    "LlamaParse quota/rate limit blocked the request. Do not enable paid overage for this benchmark."
                ) from exc
            raise ToolExecutionError(f"LlamaParse request failed: {exc}") from exc
        finally:
            try:
                client.close()
            except Exception:
                pass

        latency = time.monotonic() - started
        payload = _jsonable(response)
        if not isinstance(payload, dict):
            raise ToolOutputParseError("LlamaParse response is not a JSON object")
        items = payload.get("items") or {}
        markdown = payload.get("markdown") or {}
        item_pages = items.get("pages") if isinstance(items, dict) else None
        markdown_pages = markdown.get("pages") if isinstance(markdown, dict) else None
        if not item_pages and not markdown_pages:
            raise ToolOutputParseError(
                "LlamaParse result contains neither items.pages nor markdown.pages"
            )

        target = raw_dir / "response.json"
        write_json(target, payload)
        pages = pdf_page_count(pdf_path)
        credits_estimate = pages * credits_per_page
        provider_credits = _extract_usage_credits(payload)
        free_credits = float(self.config.get("free_credits_per_month", 10000))
        assume_free = bool(self.config.get("assume_free_quota_available", True))

        job = payload.get("job") or payload.get("job_metadata") or {}
        return RawToolResult(
            primary_artifact=str(target),
            artifacts=[str(target)],
            metadata={
                "mock": False,
                "network_calls": 1,
                "pages": pages,
                "tier": tier,
                "version": version,
                "credits_per_page": credits_per_page,
                "credits_estimate": credits_estimate,
                "provider_credits": provider_credits,
                "latency_seconds": latency,
                "processing_seconds": latency,
                "estimated_cost_usd": 0.0 if assume_free and credits_estimate <= free_credits else None,
                "actual_cost_usd": 0.0 if provider_credits is not None and provider_credits <= free_credits and assume_free else None,
                "pricing_basis": (
                    "LlamaParse Free plan includes 10,000 credits/month; "
                    f"configured {tier} tier budgeted at {credits_per_page:g} credits/page."
                ),
                "provider_job_id": job.get("id") if isinstance(job, dict) else None,
            },
        )

    def standardize(self, raw_result, raw_dir, assets_dir, *, document_id, pdf_path):
        payload = read_json(
            raw_dir / "mock_response.json"
            if raw_result.metadata.get("mock")
            else Path(raw_result.primary_artifact or "")
        )
        sizes = pdf_page_sizes(pdf_path)
        pages: dict[int, Page] = {
            i + 1: Page(page_number=i + 1, width=w, height=h)
            for i, (w, h) in enumerate(sizes)
        }

        markdown_by_page: dict[int, str] = {}
        md_root = payload.get("markdown") or {}
        md_pages = md_root.get("pages") if isinstance(md_root, dict) else []
        for idx, md in enumerate(md_pages or [], start=1):
            if not isinstance(md, dict):
                continue
            pn = _page_number(md, idx)
            markdown_by_page[pn] = _first_str(md, ("markdown", "md", "text", "content"))

        items_root = payload.get("items") or {}
        item_pages = items_root.get("pages") if isinstance(items_root, dict) else []
        if isinstance(item_pages, dict):
            item_pages = list(item_pages.values())

        global_order = 0
        for idx, native_page in enumerate(item_pages or [], start=1):
            if not isinstance(native_page, dict):
                continue
            page_no = _page_number(native_page, idx)
            if page_no not in pages:
                pages[page_no] = Page(page_number=page_no, width=1.0, height=1.0)
            page = pages[page_no]
            native_items = native_page.get("items") or native_page.get("elements") or native_page.get("blocks") or []
            if isinstance(native_items, dict):
                native_items = list(native_items.values())

            for native in native_items:
                if not isinstance(native, dict):
                    continue
                kind = _item_type(native)
                eid = str(native.get("id") or f"llamaparse_p{page_no}_{global_order:06d}")
                bbox = _item_bbox(native, page.width, page.height)
                text = _item_text(native)
                provenance = {"source": "llamaparse.items", "native_type": kind}

                if "table" in kind:
                    page.tables.append(_native_table(native, page_no, eid, bbox, global_order))
                    page.reading_order.append(eid)
                elif any(token in kind for token in ("formula", "equation", "math")):
                    latex = _first_str(native, ("latex", "tex", "math", "value"))
                    page.formulas.append(Formula(
                        element_id=eid,
                        page_number=page_no,
                        bbox=bbox,
                        raw_text=text or latex or None,
                        latex=latex or None,
                        plain_text=text or None,
                        formula_type="display",
                        order_index=global_order,
                        provenance=provenance,
                    ))
                    page.reading_order.append(eid)
                elif any(token in kind for token in ("chart", "diagram", "flowchart", "graph", "scheme")):
                    diagram_type = "chart" if any(t in kind for t in ("chart", "graph")) else ("flowchart" if "flow" in kind else "scientific_scheme")
                    page.diagrams.append(DiagramObject(
                        element_id=eid,
                        page_number=page_no,
                        bbox=bbox,
                        diagram_type=diagram_type,
                        caption=_caption(native, page.width, page.height),
                        text_elements=[text] if text else [],
                        key_elements=[],
                        order_index=global_order,
                        provenance=provenance,
                    ))
                    page.reading_order.append(eid)
                elif any(token in kind for token in ("image", "picture", "figure")):
                    caption = _caption(native, page.width, page.height)
                    # A figure explicitly described as a chart/diagram is a diagram, not a generic image.
                    semantic = " ".join(filter(None, [text, caption.text if caption else ""])).lower()
                    if any(t in semantic for t in ("chart", "diagram", "график", "диаграм", "схем")):
                        page.diagrams.append(DiagramObject(
                            element_id=eid,
                            page_number=page_no,
                            bbox=bbox,
                            diagram_type="chart" if any(t in semantic for t in ("chart", "график")) else "scientific_scheme",
                            caption=caption,
                            text_elements=[text] if text else [],
                            key_elements=[],
                            order_index=global_order,
                            provenance=provenance,
                        ))
                    else:
                        page.images.append(ImageObject(
                            element_id=eid,
                            page_number=page_no,
                            bbox=bbox,
                            asset_path=None,
                            caption=caption,
                            extraction_success=False,
                            order_index=global_order,
                            provenance=provenance,
                        ))
                    page.reading_order.append(eid)
                elif text:
                    block_type = "unknown"
                    if any(t in kind for t in ("heading", "title")):
                        block_type = "heading"
                    elif "list" in kind:
                        block_type = "list_item"
                    elif "caption" in kind:
                        block_type = "caption"
                    elif "header" in kind:
                        block_type = "header"
                    elif "footer" in kind:
                        block_type = "footer"
                    elif any(t in kind for t in ("text", "paragraph", "body", "line")) or not kind:
                        block_type = "paragraph"
                    page.text_blocks.append(TextBlock(
                        element_id=eid,
                        page_number=page_no,
                        bbox=bbox,
                        raw_text=text,
                        block_type=block_type,
                        order_index=global_order,
                        provenance=provenance,
                    ))
                    page.reading_order.append(eid)
                global_order += 1

        # Markdown is a provider-native fallback if structured items omit a class.
        table_pattern = re.compile(r"(?:^|\n)(\|[^\n]+\|\n\|\s*:?-{3,}[^\n]*\|(?:\n\|[^\n]+\|)+)", re.MULTILINE)
        display_math = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
        inline_math = re.compile(r"(?<!\$)\$([^\n$]+?)\$(?!\$)")
        for page_no, md in markdown_by_page.items():
            if page_no not in pages:
                pages[page_no] = Page(page_number=page_no, width=1.0, height=1.0)
            page = pages[page_no]
            if md and not page.text_blocks:
                eid = f"llamaparse_md_p{page_no}_text"
                page.text_blocks.append(TextBlock(
                    element_id=eid,
                    page_number=page_no,
                    raw_text=md,
                    block_type="paragraph",
                    order_index=global_order,
                    provenance={"source": "llamaparse.markdown_fallback"},
                ))
                page.reading_order.append(eid)
                global_order += 1
            if md and not page.tables:
                for ti, match in enumerate(table_pattern.finditer(md)):
                    rows, cols, cells = _parse_markdown_table(match.group(1))
                    if cells:
                        eid = f"llamaparse_md_p{page_no}_table_{ti}"
                        page.tables.append(Table(
                            element_id=eid, page_number=page_no, rows=rows, columns=cols,
                            cells=cells, order_index=global_order,
                            provenance={"source": "llamaparse.markdown_fallback"},
                        ))
                        page.reading_order.append(eid)
                        global_order += 1
            if md and not page.formulas:
                formulas = [(m.group(1), "display") for m in display_math.finditer(md)]
                formulas += [(m.group(1), "inline") for m in inline_math.finditer(md)]
                for fi, (latex, ftype) in enumerate(formulas):
                    eid = f"llamaparse_md_p{page_no}_formula_{fi}"
                    page.formulas.append(Formula(
                        element_id=eid, page_number=page_no, latex=latex.strip(), raw_text=latex.strip(),
                        formula_type=ftype, order_index=global_order,
                        provenance={"source": "llamaparse.markdown_fallback"},
                    ))
                    page.reading_order.append(eid)
                    global_order += 1

        # Do not infer chemistry from generic prose/figures. Unsupported chemical
        # objects must remain missing and receive zero under the benchmark policy.
        result_pages = [pages[k] for k in sorted(pages)]
        if not result_pages or not any(
            p.text_blocks or p.tables or p.formulas or p.images or p.diagrams
            for p in result_pages
        ):
            raise ToolOutputParseError("LlamaParse result contains no standardizable content")

        return StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path),
            tool=self.tool_metadata(),
            pages=result_pages,
            metadata={"cloud_execution": raw_result.metadata},
        )
