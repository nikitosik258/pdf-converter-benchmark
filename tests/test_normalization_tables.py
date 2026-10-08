from decimal import Decimal

from pdf_benchmark.models import Caption, Table, TableCell
from pdf_benchmark.normalization.tables import (
    canonical_table_matrix,
    normalize_table,
    parse_numeric_value,
)


def test_table_cell_text_is_normalized_but_structure_preserved():
    table = Table(
        element_id="t1",
        page_number=1,
        rows=2,
        columns=3,
        cells=[
            TableCell(
                row_index=0,
                column_index=0,
                column_span=2,
                text="  Header\u00a0name ",
                is_header=True,
            ),
            TableCell(row_index=1, column_index=0, text="0,34"),
            TableCell(row_index=1, column_index=2, text=" value  "),
        ],
        caption=Caption(text="Рис.  1.  Caption"),
    )
    n = normalize_table(table)

    assert n.rows == 2
    assert n.columns == 3
    assert n.cells[0].column_span == 2
    assert n.cells[0].normalized_text == "Header name"
    assert n.caption.normalized_text == "Рис. 1. Caption"

    # Original object is untouched.
    assert table.cells[0].normalized_text is None


def test_table_matrix_preserves_merged_cell_coverage():
    table = Table(
        element_id="t1",
        page_number=1,
        rows=2,
        columns=3,
        cells=[
            TableCell(row_index=0, column_index=0, column_span=2, text="H"),
            TableCell(row_index=0, column_index=2, text="X"),
            TableCell(row_index=1, column_index=0, text="1"),
            TableCell(row_index=1, column_index=1, text="2"),
            TableCell(row_index=1, column_index=2, text="3"),
        ],
    )
    n = normalize_table(table)
    assert canonical_table_matrix(n) == [
        ["H", None, "X"],
        ["1", "2", "3"],
    ]


def test_numeric_parser_is_separate_from_cell_text_normalization():
    assert parse_numeric_value("0,34") == Decimal("0.34")
    assert parse_numeric_value("0.34") == Decimal("0.34")
    assert parse_numeric_value("1,234.5") is None
