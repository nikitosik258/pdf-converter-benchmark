from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pdf_benchmark.models import (
    ChemicalObject,
    DiagramObject,
    Formula,
    ImageObject,
    StandardizedDocument,
    Table,
    TextBlock,
)


def _is_absolute_path(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _safe_asset_path(value: str | None) -> str | None:
    if not value:
        return value
    if _is_absolute_path(value):
        return Path(value).name
    clean = value.replace("\\", "/")
    if ".." in PurePosixPath(clean).parts:
        return PurePosixPath(clean).name
    return clean


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    blocked = ("secret", "token", "credential", "api_key", "password")
    if any(marker in key.casefold() for marker in blocked):
        return None
    if isinstance(value, dict):
        return {
            child_key: sanitized
            for child_key, child_value in value.items()
            if (sanitized := _sanitize_value(child_value, key=str(child_key))) is not None
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str) and _is_absolute_path(value):
        return Path(value).name
    return value


def public_document_payload(
    document: StandardizedDocument, *, original_filename: str
) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    payload["source_pdf"] = original_filename
    payload["raw_artifacts"] = []

    for page in payload.get("pages", []):
        for collection in ("chemical_objects", "images", "diagrams"):
            for item in page.get(collection, []):
                if "asset_path" in item:
                    item["asset_path"] = _safe_asset_path(item.get("asset_path"))

    payload = _sanitize_value(payload)
    if isinstance(payload.get("metadata"), dict):
        payload["metadata"].pop("source_pdf", None)
    return payload


def _table_text(table: Table) -> str:
    if not table.cells:
        return table.caption.text if table.caption else "[TABLE]"
    rows = max(table.rows, max((cell.row_index for cell in table.cells), default=-1) + 1)
    columns = max(
        table.columns, max((cell.column_index for cell in table.cells), default=-1) + 1
    )
    grid = [["" for _ in range(columns)] for _ in range(rows)]
    for cell in table.cells:
        grid[cell.row_index][cell.column_index] = cell.text
    lines = ["\t".join(value.strip() for value in row).rstrip() for row in grid]
    caption = table.caption.text.strip() if table.caption else ""
    return "\n".join(([caption] if caption else []) + lines).strip()


def _object_text(item: object) -> str:
    if isinstance(item, TextBlock):
        return item.raw_text or item.normalized_text or ""
    if isinstance(item, Table):
        return _table_text(item)
    if isinstance(item, Formula):
        return item.latex or item.raw_text or item.plain_text or "[FORMULA]"
    if isinstance(item, ChemicalObject):
        value = item.raw_formula or item.normalized_formula or " ".join(item.text_labels)
        return value or "[CHEMICAL STRUCTURE]"
    if isinstance(item, ImageObject):
        caption = item.caption.text if item.caption else ""
        return f"[IMAGE{': ' + caption if caption else ''}]"
    if isinstance(item, DiagramObject):
        caption = item.caption.text if item.caption else ""
        return f"[DIAGRAM{': ' + caption if caption else ''}]"
    return ""


def render_text(document: StandardizedDocument) -> str:
    pages: list[str] = []
    for page in sorted(document.pages, key=lambda current: current.page_number):
        objects = [
            *page.text_blocks,
            *page.tables,
            *page.formulas,
            *page.chemical_objects,
            *page.images,
            *page.diagrams,
        ]
        by_id = {item.element_id: item for item in objects}
        ordered: list[object] = []
        used: set[str] = set()
        for element_id in page.reading_order:
            item = by_id.get(element_id)
            if item is not None and element_id not in used:
                ordered.append(item)
                used.add(element_id)
        ordered.extend(
            sorted(
                (item for item in objects if item.element_id not in used),
                key=lambda item: (
                    item.order_index is None,
                    item.order_index if item.order_index is not None else 10**9,
                    item.element_id,
                ),
            )
        )
        chunks = [text.strip() for item in ordered if (text := _object_text(item).strip())]
        pages.append(f"--- Page {page.page_number} ---\n" + "\n\n".join(chunks))
    return "\n\n".join(pages).rstrip() + "\n"
