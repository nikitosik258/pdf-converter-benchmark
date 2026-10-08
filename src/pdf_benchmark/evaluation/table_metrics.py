from __future__ import annotations

from pdf_benchmark.models import Table, TableCell
from pdf_benchmark.normalization.tables import normalize_table

from .common import (
    mean,
    normalized_edit_similarity,
    set_precision_recall_f1,
    weighted_mean_available,
)


TABLE_WEIGHTS = {
    "cell_f1": 0.30,
    "cell_content": 0.25,
    "structure": 0.20,
    "teds_like": 0.25,
}


def _cell_key(cell: TableCell) -> tuple[int, int, int, int]:
    return (
        cell.row_index,
        cell.column_index,
        cell.row_span,
        cell.column_span,
    )


def _validate_unique_cells(table: Table) -> None:
    keys = [_cell_key(c) for c in table.cells]
    if len(keys) != len(set(keys)):
        raise ValueError(
            f"Table {table.element_id!r} contains duplicate structural cell keys"
        )


def _count_similarity(reference: int, prediction: int) -> float:
    denom = max(reference, prediction, 1)
    return max(0.0, 1.0 - abs(reference - prediction) / denom)


def _merged_keys(table: Table) -> set[tuple[int, int, int, int]]:
    return {
        _cell_key(c)
        for c in table.cells
        if c.row_span > 1 or c.column_span > 1
    }


def _table_tokens(table: Table) -> list[str]:
    """Linearized table-tree representation used as a TEDS analogue.

    It preserves:
    - row boundaries;
    - header/data distinction;
    - row/column spans;
    - cell content;
    - empty rows.

    This is intentionally named teds_like rather than claiming compatibility
    with the PubTabNet TEDS implementation.
    """
    by_row: dict[int, list[TableCell]] = {}
    for cell in table.cells:
        by_row.setdefault(cell.row_index, []).append(cell)

    tokens = ["<table>"]
    for row_idx in range(table.rows):
        tokens.append("<tr>")
        for cell in sorted(
            by_row.get(row_idx, []),
            key=lambda c: (c.column_index, c.row_span, c.column_span),
        ):
            tag = "th" if cell.is_header else "td"
            text = (
                cell.normalized_text
                if cell.normalized_text is not None
                else cell.text
            )
            tokens.extend([
                f"<{tag}:r{cell.row_span}:c{cell.column_span}:x{cell.column_index}>",
                text,
                f"</{tag}>",
            ])
        tokens.append("</tr>")
    tokens.append("</table>")
    return tokens


def calculate_table_metrics(
    reference: Table,
    prediction: Table | None,
) -> dict[str, float]:
    ref = normalize_table(reference)
    pred = (
        normalize_table(prediction)
        if prediction is not None
        else Table(
            element_id="__missing__",
            page_number=reference.page_number,
            rows=0,
            columns=0,
            cells=[],
        )
    )
    _validate_unique_cells(ref)
    _validate_unique_cells(pred)

    ref_by_key = {_cell_key(c): c for c in ref.cells}
    pred_by_key = {_cell_key(c): c for c in pred.cells}

    cell_precision, cell_recall, cell_f1 = set_precision_recall_f1(
        ref_by_key,
        pred_by_key,
    )

    if ref_by_key:
        content_scores = []
        for key, ref_cell in ref_by_key.items():
            pred_cell = pred_by_key.get(key)
            pred_text = (
                pred_cell.normalized_text
                if pred_cell is not None and pred_cell.normalized_text is not None
                else (pred_cell.text if pred_cell is not None else "")
            )
            ref_text = (
                ref_cell.normalized_text
                if ref_cell.normalized_text is not None
                else ref_cell.text
            )
            content_scores.append(
                normalized_edit_similarity(ref_text, pred_text)
            )
        cell_content = mean(content_scores)
    else:
        cell_content = 1.0 if not pred_by_key else 0.0

    row_score = _count_similarity(ref.rows, pred.rows)
    column_score = _count_similarity(ref.columns, pred.columns)

    merged_precision, merged_recall, merged_f1 = set_precision_recall_f1(
        _merged_keys(ref),
        _merged_keys(pred),
    )
    structure = mean([row_score, column_score, merged_f1])

    from .common import normalized_edit_similarity as seq_similarity

    teds_like = seq_similarity(_table_tokens(ref), _table_tokens(pred))

    table_score = weighted_mean_available(
        {
            "cell_f1": cell_f1,
            "cell_content": cell_content,
            "structure": structure,
            "teds_like": teds_like,
        },
        TABLE_WEIGHTS,
    )

    return {
        "cell_precision": cell_precision,
        "cell_recall": cell_recall,
        "cell_f1": cell_f1,
        "cell_content": cell_content,
        "row_structure": row_score,
        "column_structure": column_score,
        "merged_precision": merged_precision,
        "merged_recall": merged_recall,
        "merged_f1": merged_f1,
        "structure": structure,
        "teds_like": teds_like,
        "table_score": table_score,
    }
