from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from .captions import normalize_caption
from .text import normalize_text

if TYPE_CHECKING:
    from pdf_benchmark.models import Table


def parse_numeric_value(value: str | None) -> Decimal | None:
    """Parse a simple decimal cell without changing its normalized text.

    This helper exists for the later Numeric Cell Match metric:
        "0,34" and "0.34" can map to Decimal("0.34")
    while Cell Text Match can still observe their punctuation difference.

    Thousands separators and ambiguous locale formats are deliberately rejected.
    """
    text = normalize_text(value, preserve_paragraphs=False)
    if not text:
        return None
    compact = text.replace(" ", "")
    if compact.count(",") > 1 or compact.count(".") > 1:
        return None
    if "," in compact and "." in compact:
        return None
    canonical = compact.replace(",", ".")
    if not __import__("re").fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", canonical):
        return None
    try:
        return Decimal(canonical)
    except InvalidOperation:
        return None


def normalize_table(table: "Table") -> "Table":
    """Return a deep-copied table with normalized cell/caption text.

    Structural information is NEVER repaired:
    - rows/columns are preserved;
    - missing/extra cells are preserved;
    - row_span/column_span are preserved;
    - cell coordinates are preserved;
    - cells are not dropped or merged.

    Cells are sorted only to obtain deterministic serialization.
    """
    normalized = table.model_copy(deep=True)

    for cell in normalized.cells:
        cell.normalized_text = normalize_text(
            cell.text,
            preserve_paragraphs=False,
        )

    normalized.cells.sort(
        key=lambda c: (
            c.row_index,
            c.column_index,
            c.row_span,
            c.column_span,
            c.text,
        )
    )

    if normalized.caption is not None:
        normalized.caption.normalized_text = normalize_caption(
            normalized.caption.text
        )

    return normalized


def canonical_table_matrix(table: "Table") -> list[list[str | None]]:
    """Build a content matrix for inspection only.

    The matrix does not duplicate merged-cell text into covered cells.
    Covered positions are None, preserving merge semantics for later metrics.
    """
    matrix: list[list[str | None]] = [
        ["" for _ in range(table.columns)] for _ in range(table.rows)
    ]

    covered: set[tuple[int, int]] = set()
    for cell in sorted(table.cells, key=lambda c: (c.row_index, c.column_index)):
        if cell.row_index >= table.rows or cell.column_index >= table.columns:
            continue

        value = (
            cell.normalized_text
            if cell.normalized_text is not None
            else normalize_text(cell.text, preserve_paragraphs=False)
        )
        matrix[cell.row_index][cell.column_index] = value

        for r in range(cell.row_index, min(table.rows, cell.row_index + cell.row_span)):
            for c in range(
                cell.column_index,
                min(table.columns, cell.column_index + cell.column_span),
            ):
                if (r, c) == (cell.row_index, cell.column_index):
                    continue
                covered.add((r, c))

    for r, c in covered:
        matrix[r][c] = None

    return matrix
