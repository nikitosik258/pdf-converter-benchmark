from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_benchmark.adapters.local import MinerUAdapter
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.utils.io import write_json


def test_mineru_standardize_synthetic_structured_content(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    saved = raw_dir / "saved"
    saved.mkdir(parents=True)
    payload = {
        "pages": [{
            "page_idx": 0,
            "blocks": [
                {"type": "paragraph", "bbox": [100,100,900,180], "content": {"paragraph_content": "Hello MinerU"}},
                {"type": "equation_interline", "bbox": [100,200,500,260], "content": {"math_content": "E=mc^2", "math_type": "latex"}},
                {"type": "table", "bbox": [100,300,900,500], "content": {"html": "<table><tr><td>x</td><td>1</td></tr></table>", "table_caption": ["Example table"]}},
            ],
        }],
        "metadata": {"producer": {"name": "mineru", "version": "4.0.0"}},
        "extensions": {},
    }
    path = write_json(saved / "structured_content.json", payload)
    result = MinerUAdapter().standardize(
        RawToolResult(primary_artifact=str(path), metadata={"saved_dir": str(saved)}),
        raw_dir, tmp_path / "assets", document_id="T01", pdf_path=tmp_path / "fake.pdf",
    )
    page = result.pages[0]
    assert page.text_blocks[0].raw_text == "Hello MinerU"
    assert page.formulas[0].latex == "E=mc^2"
    assert page.tables[0].rows == 1
    assert page.tables[0].columns == 2


def _table_from_block(tmp_path: Path, block: dict):
    saved = tmp_path / "raw" / "saved"
    saved.mkdir(parents=True)
    path = write_json(saved / "structured_content.json", {
        "pages": [{"page_idx": 0, "blocks": [block]}],
    })
    document = MinerUAdapter().standardize(
        RawToolResult(primary_artifact=str(path), metadata={"saved_dir": str(saved)}),
        saved.parent, tmp_path / "assets", document_id="T01", pdf_path=tmp_path / "unused.pdf",
    )
    return document.pages[0].tables[0]


@pytest.mark.parametrize("sample_index", [0, 1], ids=["native_markdown", "native_html"])
def test_mineru_tables_from_saved_native_responses(tmp_path, sample_index):
    fixture = Path(__file__).parent / "fixtures/local/mineru_tables.json"
    block = json.loads(fixture.read_text(encoding="utf-8"))[sample_index]["block"]
    table = _table_from_block(tmp_path, block)
    if sample_index == 0:
        assert (table.rows, table.columns, len(table.cells)) == (4, 3, 12)
        assert table.cells[0].text == ""
        assert table.cells[5].text == r"$\alpha, \lambda$"
        assert table.cells[-1].text == r"$\alpha$"
        assert sum(c.is_header for c in table.cells) == 3
        assert table.html is None
        assert table.provenance["markdown"] == block["content"]
    else:
        assert (table.rows, table.columns, len(table.cells)) == (5, 7, 31)
        assert table.cells[0].text == "Краситель"
        assert table.cells[0].row_span == 2
        assert table.cells[1].column_span == 2
        assert (table.cells[4].row_index, table.cells[4].column_index) == (1, 1)
        assert table.cells[-1].text == "20"
        assert table.html == block["content"]


def test_mineru_string_html_takes_precedence_over_pipe_characters(tmp_path):
    html = (
        '<TABLE><tr><th rowspan="2">Name</th><th colspan="2">Values</th></tr>'
        '<tr><td>A</td><td>B</td></tr>'
        '<tr><td>x|y</td><td>0,34</td><td></td></tr></TABLE>'
    )
    table = _table_from_block(tmp_path, {"type": "table", "content": html})
    assert (table.rows, table.columns, len(table.cells)) == (3, 3, 7)
    assert table.html == html
    assert (table.cells[0].row_span, table.cells[1].column_span) == (2, 2)
    assert [c.text for c in table.cells[-3:]] == ["x|y", "0,34", ""]
    assert [c.is_header for c in table.cells[:2]] == [True, True]


@pytest.mark.parametrize("location", ["content.html", "content.table_body", "html", "table_body"])
def test_mineru_legacy_html_fields_still_work(tmp_path, location):
    html = "<table><tr><td>Legacy</td><td>42</td></tr></table>"
    block = {"type": "table"}
    if location.startswith("content."):
        block["content"] = {location.split(".")[1]: html}
    else:
        block[location] = html
    table = _table_from_block(tmp_path, block)
    assert (table.rows, table.columns) == (1, 2)
    assert [c.text for c in table.cells] == ["Legacy", "42"]
    assert table.html == html


@pytest.mark.parametrize("bordered", [True, False])
def test_mineru_markdown_empty_cells_alignment_and_inline_content(tmp_path, bordered):
    lines = ["A | B | C", ":--- | ---: | :---:", r"x | | $\alpha$",
             "x<sub>1</sub> | &lt;0,5 | 0,34"]
    if bordered:
        lines = ["|" + line + "|" for line in lines]
    markdown = "\n".join(lines)
    table = _table_from_block(tmp_path, {"type": "table", "content": markdown})
    assert (table.rows, table.columns, len(table.cells)) == (3, 3, 9)
    assert [c.text for c in table.cells[3:6]] == ["x", "", r"$\alpha$"]
    assert [c.text for c in table.cells[6:]] == ["x<sub>1</sub>", "&lt;0,5", "0,34"]
    assert all(c.row_span == c.column_span == 1 for c in table.cells)


def test_mineru_markdown_preserves_empty_edge_columns(tmp_path):
    markdown = "||B||\n|---|---|---|\n||value||"
    table = _table_from_block(tmp_path, {"type": "table", "content": markdown})
    assert (table.rows, table.columns) == (2, 3)
    assert [c.text for c in table.cells] == ["", "B", "", "", "value", ""]


def test_mineru_markdown_escaped_pipes_do_not_create_columns(tmp_path):
    markdown = r"""| Variable | Value |
| --- | --- |
| $\alpha$ | $a\|b$ |
| x | trailing\| |
"""
    table = _table_from_block(tmp_path, {"type": "table", "content": markdown})
    assert (table.rows, table.columns, len(table.cells)) == (3, 2, 6)
    assert [c.text for c in table.cells[2:]] == [r"$\alpha$", "$a|b$", "x", "trailing|"]
    assert table.provenance["markdown"] == markdown


def test_mineru_markdown_ragged_rows_are_not_repaired(tmp_path):
    markdown = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 |\n| 3 | 4 | 5 | 6 |"
    table = _table_from_block(tmp_path, {"type": "table", "content": markdown})
    assert (table.rows, table.columns, len(table.cells)) == (3, 4, 9)
    positions = {(c.row_index, c.column_index) for c in table.cells}
    assert (1, 2) not in positions  # No invented cell for the short row.
    assert (2, 3) in positions  # Extra provider cells are not discarded.


def test_mineru_markdown_header_only_table(tmp_path):
    table = _table_from_block(tmp_path, {"type": "table", "content": "| A | B |\n| --- | --- |"})
    assert (table.rows, table.columns, len(table.cells)) == (1, 2, 2)


@pytest.mark.parametrize("content", ["", "A | B\nC | D", "A | B\n---"])
def test_mineru_does_not_invent_tables_from_arbitrary_text(tmp_path, content):
    table = _table_from_block(tmp_path, {"type": "table", "content": content})
    assert (table.rows, table.columns, table.cells) == (0, 0, [])
