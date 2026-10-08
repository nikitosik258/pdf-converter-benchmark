"""Address native OCR words without another API request."""
import pytest

from pdf_benchmark.adapters.cloud.mindee_adapter import MindeeOCRAdapter
from pdf_benchmark.benchmark.ground_truth import GroundTruthObject
from pdf_benchmark.benchmark.matching import match_document
from pdf_benchmark.models import BBox, RawToolResult
from pdf_benchmark.utils.io import write_json


def word(text, x, y=.1):
    return {"content": text, "polygon": [[x, y], [x+.04, y], [x+.04, y+.02], [x, y+.02]]}


def standardize(tmp_path, pages, wrapper="inference"):
    result = {"pages": pages} if wrapper != "raw_text" else {"raw_text": {"pages": pages}}
    payload = {"result": result}
    if wrapper == "inference":
        payload = {"inference": payload}
    raw = tmp_path / "raw"
    path = write_json(raw / "response.json", payload)
    return MindeeOCRAdapter().standardize(RawToolResult(primary_artifact=str(path)),
        raw, tmp_path / "assets", document_id="D1", pdf_path=tmp_path / "unused.pdf")


@pytest.mark.parametrize("wrapper", ["inference", "unwrapped", "raw_text"])
def test_native_words_preserve_text_geometry_order_and_page_text(tmp_path, wrapper):
    words = [word("same", .3), word("word", .2), word("same", .1)]
    doc = standardize(tmp_path, [{"content": "same word\nsame", "words": words}], wrapper)
    page = doc.pages[0]
    assert [b.raw_text for b in page.text_blocks] == ["same", "word", "same"]
    assert page.reading_order == ["mindee_p1_word_000000", "mindee_p1_word_000001", "mindee_p1_word_000002"]
    assert [b.order_index for b in page.text_blocks] == [0, 1, 2]
    assert [b.bbox.x_min for b in page.text_blocks] == [.3, .2, .1]
    assert [b.provenance["word"] for b in page.text_blocks] == words
    assert doc.metadata["mindee_page_text"] == {"1": "same word\nsame"}
    assert page.width == page.height == 1


@pytest.mark.parametrize("field", ["content", "text", "value"])
def test_word_content_aliases_and_missing_polygon_do_not_invent_coordinates(tmp_path, field):
    doc = standardize(tmp_path, [{"words": [{field: "one"}, word("two", .2)]}])
    blocks = doc.pages[0].text_blocks
    assert [b.raw_text for b in blocks] == ["one", "two"]
    assert blocks[0].bbox is None
    assert blocks[1].bbox is not None


@pytest.mark.parametrize("words", [[], [{"content": " "}], [{"content": "", "polygon": []}]])
def test_page_text_is_fallback_when_there_are_no_readable_words(tmp_path, words):
    doc = standardize(tmp_path, [{"text": "whole page", "words": words}])
    blocks = doc.pages[0].text_blocks
    assert len(blocks) == 1
    assert blocks[0].raw_text == "whole page"
    assert blocks[0].element_id == "mindee_p1_000000"


def test_page_ids_and_native_word_indices_survive_empty_words(tmp_path):
    doc = standardize(tmp_path, [
        {"content": "a", "words": [{"content": " "}, word("a", .1)]},
        {"content": "b", "words": [word("b", .1)]},
        {"content": "", "words": []},
    ])
    assert [p.page_number for p in doc.pages] == [1, 2, 3]
    assert doc.pages[0].reading_order == ["mindee_p1_word_000001"]
    assert doc.pages[1].reading_order == ["mindee_p2_word_000000"]
    assert doc.pages[2].text_blocks[0].raw_text == ""


def test_word_representation_matches_two_regions_without_cross_column_text(tmp_path):
    words = [word("left", .1), word("same", .2), word("right", .7), word("same", .8)]
    doc = standardize(tmp_path, [{"content": "left same right same", "words": words}])
    gt = [GroundTruthObject(object_id=eid, document_id="D1", page=1, object_type="text",
        bbox=BBox(x_min=x0, y_min=0, x_max=x1, y_max=.5), reference={"text": text})
        for eid, x0, x1, text in [("L", 0, .4, "left same"), ("R", .6, 1, "right same")]]
    matches = match_document(gt, doc)
    assert [m.prediction["text"] for m in matches] == ["left same", "right same"]
    ids = [eid for m in matches for eid in m.context["source_element_ids"]]
    assert len(ids) == len(set(ids)) == 4


def test_dictionary_polygon_and_extra_native_word_fields_are_preserved(tmp_path):
    native_word = {"content": "axis", "polygon": [
        {"x": .1, "y": .2}, {"x": .3, "y": .19},
        {"x": .31, "y": .25}, {"x": .11, "y": .26}], "confidence": .87}
    doc = standardize(tmp_path, [{"words": [native_word]}])
    block = doc.pages[0].text_blocks[0]
    assert block.bbox == BBox(x_min=.1, y_min=.19, x_max=.31, y_max=.26)
    assert block.provenance["word"] == native_word
