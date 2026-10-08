"""PyMuPDF native line/span boundaries survive standardization."""
from pathlib import Path

from pdf_benchmark.adapters.local.pymupdf_adapter import PyMuPDFAdapter
from pdf_benchmark.benchmark.ground_truth import GroundTruthObject
from pdf_benchmark.benchmark.matching import match_document
from pdf_benchmark.models import BBox, RawToolResult
from pdf_benchmark.utils.io import write_json


def span(text, bbox=None, font="Regular"):
    return {"text": text, "bbox": bbox, "font": font, "size": 10}


def line(spans, bbox=None, **extra):
    return {"spans": spans, "bbox": bbox, "dir": [1, 0], "wmode": 0, **extra}


def standardize(tmp_path, blocks):
    raw = tmp_path / "raw"
    path = write_json(raw / "pymupdf_raw.json", {
        "pages": [{"page_number": 1, "width": 100, "height": 100,
                   "ocr_used": False, "text_json": {"blocks": blocks}, "tables": []}],
    })
    assets = tmp_path / "assets"
    assets.mkdir()
    return PyMuPDFAdapter().standardize(
        RawToolResult(primary_artifact=str(path)), raw, assets,
        document_id="D1", pdf_path=Path("unused.pdf"),
    ).pages[0]


def text_block(lines, bbox=(10, 10, 90, 90), number=7):
    return {"type": 0, "bbox": list(bbox), "number": number, "lines": lines}


def test_font_spans_are_concatenated_without_invented_spaces(tmp_path):
    native_spans = [span("micro", [10, 10, 30, 20]), span("scope", [30, 10, 60, 20], "Bold")]
    page = standardize(tmp_path, [text_block([line(native_spans, [10, 10, 60, 20])])])
    block = page.text_blocks[0]
    assert block.raw_text == "microscope"
    assert block.provenance["spans"] == native_spans
    assert block.provenance["text_assembly"] == "native_span_concatenation_v1"


def test_native_space_between_spans_is_preserved_once(tmp_path):
    page = standardize(tmp_path, [text_block([
        line([span("alpha "), span("beta", font="Italic")], [10, 10, 60, 20]),
    ])])
    assert page.text_blocks[0].raw_text == "alpha beta"


def test_each_native_line_is_an_addressable_text_block(tmp_path):
    lines = [
        line([span("first")], [10, 10, 60, 20]),
        line([span("second")], [10, 30, 60, 40]),
    ]
    page = standardize(tmp_path, [text_block(lines, number=13)])
    assert [b.raw_text for b in page.text_blocks] == ["first", "second"]
    assert [b.element_id for b in page.text_blocks] == ["p1_text_0_line_0", "p1_text_0_line_1"]
    assert page.reading_order == ["p1_text_0_line_0", "p1_text_0_line_1"]
    assert [b.provenance["pymupdf_line_index"] for b in page.text_blocks] == [0, 1]
    assert all(b.provenance["pymupdf_block_number"] == 13 for b in page.text_blocks)


def test_line_bbox_is_used_instead_of_parent_block_bbox(tmp_path):
    page = standardize(tmp_path, [text_block([
        line([span("first")], [10, 20, 60, 30]),
    ], bbox=(0, 0, 100, 100))])
    assert page.text_blocks[0].bbox == BBox(x_min=.1, y_min=.2, x_max=.6, y_max=.3)


def test_missing_line_bbox_is_union_of_span_bboxes(tmp_path):
    page = standardize(tmp_path, [text_block([line([
        span("a", [10, 20, 20, 30]), span("b", [20, 18, 40, 32]),
    ])])])
    assert page.text_blocks[0].bbox == BBox(x_min=.1, y_min=.18, x_max=.4, y_max=.32)


def test_missing_line_and_span_boxes_fall_back_to_parent_bbox(tmp_path):
    page = standardize(tmp_path, [text_block([line([span("text")])], bbox=(5, 6, 70, 80))])
    assert page.text_blocks[0].bbox == BBox(x_min=.05, y_min=.06, x_max=.7, y_max=.8)


def test_empty_native_lines_do_not_renumber_later_source_lines(tmp_path):
    page = standardize(tmp_path, [text_block([
        line([span(" ")], [10, 10, 20, 20]),
        line([span("kept")], [10, 30, 40, 40]),
    ])])
    assert [b.element_id for b in page.text_blocks] == ["p1_text_0_line_1"]
    assert page.reading_order == ["p1_text_0_line_1"]


def test_repeated_text_at_different_line_coordinates_is_preserved(tmp_path):
    page = standardize(tmp_path, [text_block([
        line([span("same")], [10, 10, 40, 20]),
        line([span("same")], [10, 30, 40, 40]),
    ])])
    assert [b.raw_text for b in page.text_blocks] == ["same", "same"]
    assert page.text_blocks[0].bbox != page.text_blocks[1].bbox


def test_line_boundaries_allow_region_assembly_without_cross_column_text(tmp_path):
    page = standardize(tmp_path, [text_block([
        line([span("electro-")], [0, 10, 40, 20]),
        line([span("conductivity")], [0, 30, 40, 40]),
        line([span("other column")], [60, 10, 100, 20]),
    ], bbox=(0, 0, 100, 50))])
    # Reuse the constructed page inside the minimal document contract.
    from pdf_benchmark.models import StandardizedDocument, ToolMetadata
    document = StandardizedDocument(document_id="D1", source_pdf="unused.pdf",
        tool=ToolMetadata(tool_name="pymupdf", distribution_name="PyMuPDF"), pages=[page])
    gt = GroundTruthObject(object_id="T", document_id="D1", page=1, object_type="text",
        bbox=BBox(x_min=0, y_min=0, x_max=.4, y_max=.5), reference={"text": "electro-conductivity"})
    match = match_document([gt], document)[0]
    assert match.prediction["text"] == "electro-conductivity"
    assert match.context["source_element_ids"] == ["p1_text_0_line_0", "p1_text_0_line_1"]


def test_line_direction_and_writing_mode_are_retained(tmp_path):
    native = line([span("vertical")], [10, 10, 20, 60], dir=[0, -1], wmode=1)
    block = standardize(tmp_path, [text_block([native])]).text_blocks[0]
    assert block.provenance["line_direction"] == [0, -1]
    assert block.provenance["writing_mode"] == 1


def test_native_sorted_line_order_is_not_resorted_by_coordinates(tmp_path):
    page = standardize(tmp_path, [text_block([
        line([span("native first")], [10, 50, 60, 60]),
        line([span("native second")], [10, 10, 60, 20]),
    ])])
    assert page.reading_order == ["p1_text_0_line_0", "p1_text_0_line_1"]
