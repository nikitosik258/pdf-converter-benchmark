import pytest

from pdf_benchmark.models import Table, TableCell
from pdf_benchmark.evaluation.table_metrics import calculate_table_metrics


def table(values):
    cells = []
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            cells.append(
                TableCell(
                    row_index=r,
                    column_index=c,
                    text=value,
                    is_header=(r == 0),
                )
            )
    return Table(
        element_id="t",
        page_number=1,
        rows=len(values),
        columns=len(values[0]) if values else 0,
        cells=cells,
    )


def test_table_ideal():
    t = table([["A", "B"], ["1", "2"]])
    m = calculate_table_metrics(t, t)
    assert m["cell_precision"] == 1.0
    assert m["cell_recall"] == 1.0
    assert m["cell_f1"] == 1.0
    assert m["table_score"] == 1.0


def test_table_partial_content_error():
    ref = table([["A", "B"], ["1", "2"]])
    pred = table([["A", "B"], ["1", "9"]])
    m = calculate_table_metrics(ref, pred)
    assert m["cell_f1"] == 1.0  # structure exists
    assert 0.0 < m["cell_content"] < 1.0
    assert 0.0 < m["table_score"] < 1.0


def test_table_missing_prediction_is_bad():
    ref = table([["A", "B"], ["1", "2"]])
    m = calculate_table_metrics(ref, None)
    assert m["cell_recall"] == 0.0
    assert m["cell_f1"] == 0.0
    assert m["table_score"] < 0.2


def test_table_duplicate_structural_cells_rejected():
    bad = Table(
        element_id="bad",
        page_number=1,
        rows=1,
        columns=1,
        cells=[
            TableCell(row_index=0, column_index=0, text="A"),
            TableCell(row_index=0, column_index=0, text="B"),
        ],
    )
    with pytest.raises(ValueError):
        calculate_table_metrics(bad, bad)
