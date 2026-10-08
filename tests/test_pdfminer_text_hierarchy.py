"""Choose one text hierarchy level using source structure, not repeated strings."""
from pathlib import Path

import pytest
from pdfminer.layout import LTAnno, LTContainer, LTPage, LTTextBoxHorizontal, LTTextLineHorizontal

from pdf_benchmark.adapters.local.pdfminer_adapter import PdfMinerAdapter
from pdf_benchmark.adapters.base import ToolOutputParseError
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.utils.io import read_json, write_json


def line(text, y=10, vertical=False, **extra):
    return {"text": text, "bbox": [10, y, 50, y+10],
            "layout_type": "LTTextLineVertical" if vertical else "LTTextLineHorizontal", **extra}


def box(text="one two", vertical=False, **extra):
    return {"text": text, "bbox": [10, 10, 50, 40],
            "layout_type": "LTTextBoxVertical" if vertical else "LTTextBoxHorizontal", **extra}


def standardize(tmp_path, blocks):
    raw = tmp_path / "raw"
    path = write_json(raw / "pdfminer_raw.json", {"pages": [
        {"page_number": 1, "width": 100, "height": 100, "text_blocks": blocks},
    ]})
    return PdfMinerAdapter().standardize(RawToolResult(primary_artifact=str(path)),
        raw, tmp_path / "assets", document_id="D1", pdf_path=tmp_path / "unused.pdf").pages[0]


@pytest.mark.parametrize("vertical", [False, True])
def test_legacy_cache_keeps_lines_once_with_original_indices_and_coordinates(tmp_path, vertical):
    blocks = [box(vertical=vertical), line("one", vertical=vertical), line("two", 30, vertical=vertical)]
    page = standardize(tmp_path, blocks)
    assert [b.raw_text for b in page.text_blocks] == ["one", "two"]
    assert page.reading_order == ["p1_txt_0001", "p1_txt_0002"]
    assert [(b.bbox.y_min, b.bbox.y_max) for b in page.text_blocks] == [(.8, .9), (.6, .7)]
    assert [b.provenance["parent_text_index"] for b in page.text_blocks] == [0, 0]


def test_same_text_at_different_positions_remains_repeated(tmp_path):
    page = standardize(tmp_path, [box("same same"), line("same"), line("same", 30)])
    assert [b.raw_text for b in page.text_blocks] == ["same", "same"]


def test_matching_line_after_completed_parent_is_an_independent_object(tmp_path):
    page = standardize(tmp_path, [box("one"), line("one"), line("one", 60)])
    assert [b.raw_text for b in page.text_blocks] == ["one", "one"]
    assert page.reading_order == ["p1_txt_0001", "p1_txt_0002"]


@pytest.mark.parametrize("child", [None, line("other"), line("one", 60), line("one")])
def test_unproven_or_incomplete_legacy_parent_is_not_silently_dropped(tmp_path, child):
    blocks = [box("one two")]
    if child is not None:
        blocks.append(child)
    page = standardize(tmp_path, blocks)
    assert page.text_blocks[0].raw_text == "one two"
    assert page.text_blocks[0].element_id == "p1_txt_0000"


def test_explicit_parent_relationships_do_not_require_adjacent_records(tmp_path):
    blocks = [box(parent_text_index=None), line("one", parent_text_index=0),
              line("independent", 60, parent_text_index=None), line("two", 30, parent_text_index=0)]
    page = standardize(tmp_path, blocks)
    assert [b.raw_text for b in page.text_blocks] == ["one", "independent", "two"]
    assert page.reading_order == ["p1_txt_0001", "p1_txt_0002", "p1_txt_0003"]


def test_explicit_roots_are_not_inferred_to_be_children_from_equal_text(tmp_path):
    blocks = [box("one", parent_text_index=None), line("one", parent_text_index=None)]
    page = standardize(tmp_path, blocks)
    assert len(page.text_blocks) == 2


def test_future_raw_records_explicit_parentage_without_real_pdf_acquisition(tmp_path, monkeypatch):
    parent = LTTextBoxHorizontal()
    for text, y in [("one", 10), ("two", 30)]:
        child = LTTextLineHorizontal(word_margin=.1)
        LTContainer.add(child, LTAnno(text + "\n"))
        child.set_bbox((10, y, 50, y+10))
        parent.add(child)
    native = LTPage(1, (0, 0, 100, 100))
    native.add(parent)
    monkeypatch.setattr("pdfminer.high_level.extract_pages", lambda *args, **kwargs: [native])
    adapter = PdfMinerAdapter()
    raw = tmp_path / "raw"
    result = adapter.run_raw(Path("unused.pdf"), raw)
    data = read_json(result.primary_artifact)
    blocks = data["pages"][0]["text_blocks"]
    assert [b["parent_text_index"] for b in blocks] == [None, 0, 0]
    assert [b["text"] for b in blocks] == ["one two", "one", "two"]
    doc = adapter.standardize(result, raw, tmp_path / "assets", document_id="D1", pdf_path=Path("unused.pdf"))
    assert [b.raw_text for b in doc.pages[0].text_blocks] == ["one", "two"]


@pytest.mark.parametrize("parent", [-1, 1, "0", True])
def test_invalid_explicit_parent_index_is_rejected(tmp_path, parent):
    with pytest.raises(ToolOutputParseError, match="Invalid pdfminer text parent"):
        standardize(tmp_path, [box(parent_text_index=None), line("one", parent_text_index=parent)])


def test_nested_explicit_hierarchy_retains_only_fully_represented_leaves(tmp_path):
    page = standardize(tmp_path, [box(parent_text_index=None),
        box("one", parent_text_index=0), line("one", parent_text_index=1),
        box("two", parent_text_index=0), line("two", 30, parent_text_index=3)])
    assert [b.raw_text for b in page.text_blocks] == ["one", "two"]
    assert page.reading_order == ["p1_txt_0002", "p1_txt_0004"]
