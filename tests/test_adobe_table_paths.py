"""Adobe table-root regressions; use local JSON/CSV and never call the API."""
from __future__ import annotations

import csv
import json

import pytest

from pdf_benchmark.adapters.cloud.adobe_extract_adapter import AdobeExtractAdapter
from pdf_benchmark.models import RawToolResult


def _element(path, *, page=0, csv_name="table.csv", text=None):
    element = {
        "Path": path, "Page": page, "Bounds": [59.5, 84.2, 297.5, 421],
        "filePaths": [f"tables/{csv_name}"],
    }
    if text is not None:
        element["Text"] = text
    return element


def _standardize(tmp_path, elements, csv_files):
    raw = tmp_path / "raw"
    extracted = raw / "extracted"
    (extracted / "tables").mkdir(parents=True)
    for name, rows in csv_files.items():
        with (extracted / "tables" / name).open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows(rows)
    payload = {
        "pages": [{"pageNumber": n, "width": 595, "height": 842}
                  for n in sorted({el["Page"] for el in elements})],
        "elements": elements,
    }
    source = extracted / "structuredData.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    return AdobeExtractAdapter().standardize(
        RawToolResult(primary_artifact=str(source)), raw, tmp_path / "assets",
        document_id="T01", pdf_path=tmp_path / "unused.pdf",
    )


def test_adobe_indexed_tables_retain_their_own_csv_and_child_text(tmp_path):
    # Real responses use both //Document/Table and //Document/Table[2].
    first = [["A", "B"], ["1", "2"]]
    second = [["Name", "Value"], ["0,34", ""], ["first\nsecond", "a,b"]]
    elements = [
        _element("//Document/Table", csv_name="first.csv"),
        _element("//Document/Table/TR/TD/P", csv_name="first.csv", text="First child"),
        _element("//Document/Table[2]", page=1, csv_name="second.csv"),
        _element("//Document/Table[2]/TR[2]/TD/P", page=1,
                 csv_name="second.csv", text="Second child"),
    ]
    doc = _standardize(tmp_path, elements, {"first.csv": first, "second.csv": second})
    assert [len(page.tables) for page in doc.pages] == [1, 1]
    for page, expected, root_index in zip(doc.pages, [first, second], [0, 2]):
        table = page.tables[0]
        assert (table.rows, table.columns) == (len(expected), 2)
        assert [c.text for c in table.cells] == [value for row in expected for value in row]
        assert table.provenance["path"] == elements[root_index]["Path"]
        assert table.element_id == f"adobe_p{page.page_number}_{root_index:05d}"
        assert table.order_index == root_index
        box = table.bbox
        assert (box.x_min, box.y_min, box.x_max, box.y_max) == pytest.approx((.1, .5, .5, .9))
        assert page.reading_order == [table.element_id, page.text_blocks[0].element_id]
    assert [p.text_blocks[0].raw_text for p in doc.pages] == ["First child", "Second child"]


@pytest.mark.parametrize("path,is_table", [
    ("//Document/Table", True),
    ("//Document/Table[2]", True),
    ("//Document/Table[12]", True),
    ("//Document/L[2]/LI/LBody/Table[3]", True),
    ("//Document/table[4]/", True),
    ("//Document/Table[2]/TR[1]", False),
    ("//Document/Table[2]/TR/TD/P", False),
    ("//Document/Table[2]/Figure", False),
    ("//Document/TableCaption", False),
    ("//Document/Table[2]suffix", False),
    ("//Document/TableDataTable", False),
])
def test_adobe_only_terminal_table_segments_create_tables(tmp_path, path, is_table):
    doc = _standardize(tmp_path, [_element(path)], {"table.csv": [["Value"], ["42"]]})
    assert len(doc.pages[0].tables) == int(is_table)
    if is_table:
        assert [c.text for c in doc.pages[0].tables[0].cells] == ["Value", "42"]


def test_adobe_duplicate_indexed_root_is_not_counted_twice(tmp_path):
    element = _element("//Document/Table[2]")
    doc = _standardize(tmp_path, [element, element], {"table.csv": [["42"]]})
    page = doc.pages[0]
    assert len(page.tables) == 1
    assert page.reading_order == [page.tables[0].element_id]


def test_adobe_indexed_root_without_csv_preserves_detection_without_inventing_cells(tmp_path):
    doc = _standardize(tmp_path, [_element("//Document/Table[2]", csv_name="missing.csv")], {})
    assert len(doc.pages[0].tables) == 1
    table = doc.pages[0].tables[0]
    assert table.bbox is not None
    assert (table.rows, table.columns, table.cells) == (0, 0, [])
    assert table.provenance["csv"] is None
