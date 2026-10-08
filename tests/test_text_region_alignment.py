"""Region completion must depend on geometry, never on the resulting score."""
import pytest

from pdf_benchmark.benchmark.ground_truth import GroundTruthObject
from pdf_benchmark.benchmark.matching import MatchingConfig, match_document
from pdf_benchmark.evaluation.text_metrics import calculate_text_metrics
from pdf_benchmark.models import BBox, Page, StandardizedDocument, TextBlock, ToolMetadata


def box(x0, y0, x1, y1):
    return BBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1)


def region(oid="A", bbox=None, text="first second"):
    return GroundTruthObject(object_id=oid, document_id="D1", page=1,
                             object_type="text", bbox=bbox or box(0, 0, .4, 1), reference={"text": text})


def block(eid, bbox, text=None):
    return TextBlock(element_id=eid, page_number=1, bbox=bbox, raw_text=text or eid)


def document(blocks, order=None):
    return StandardizedDocument(document_id="D1", source_pdf="unused.pdf",
        tool=ToolMetadata(tool_name="fake", distribution_name="fake"),
        pages=[Page(page_number=1, width=100, height=100, text_blocks=blocks, reading_order=order or [])])


def fragments():
    return [block("first", box(0, 0, .4, .35)), block("second", box(0, .5, .4, .75))]


def test_hungarian_fragment_is_completed_and_scored_as_region():
    result = match_document([region()], document(fragments()))[0]
    assert result.match_method == "text_multiblock_overlap"
    assert result.context["hungarian_element_id"] == "first"
    assert result.context["source_element_ids"] == ["first", "second"]
    assert result.prediction["text"] == "first second"
    assert calculate_text_metrics("first second", result.prediction["text"])["text_score"] == 1


@pytest.mark.parametrize("reference", ["first", "second", "entirely unrelated GT"])
def test_completion_does_not_pick_the_text_with_the_best_score(reference):
    result = match_document([region(text=reference)], document(fragments()))[0]
    assert result.prediction["text"] == "first second"


def test_complete_single_block_is_preserved_despite_nested_text():
    result = match_document([region()], document([
        block("whole", box(0, 0, .4, 1), "first second"), *fragments(),
    ]))[0]
    assert result.prediction_element_id == "whole"
    assert result.match_method == "bbox_iou"


def test_fragment_containing_only_nested_duplicates_is_not_extended():
    result = match_document([region()], document([
        fragments()[0], block("child", box(.1, .1, .2, .2)),
    ]))[0]
    assert result.prediction_element_id == "first"


def test_completion_preserves_order_and_repeated_words_at_distinct_boxes():
    blocks = [*fragments(), block("repeat", box(0, .8, .4, .95), "first")]
    result = match_document([region()], document(blocks, ["repeat", "first", "second"]))[0]
    assert result.prediction["text"] == "first first second"
    assert result.context["source_element_ids"] == ["repeat", "first", "second"]


def test_completion_does_not_admit_cross_column_line_below_overlap_threshold():
    blocks = [*fragments(), block("other column", box(.25, .8, .9, .9))]
    result = match_document([region()], document(blocks))[0]
    assert result.prediction["text"] == "first second"
    assert result.context["selection_threshold"] == MatchingConfig().text_multiblock_min_overlap == .5


def test_completion_does_not_consume_another_hungarian_anchor():
    gt = [region(), region("B", box(0, .5, .4, .75), "second")]
    results = match_document(gt, document(fragments()))
    assert [r.prediction_element_id for r in results] == ["first", "second"]


def test_no_bbox_hungarian_assignment_is_not_replaced_with_spatial_text():
    result = match_document([region()], document([
        block("exact", None, "first second"), *fragments(),
    ]))[0]
    assert result.prediction_element_id == "exact"
    assert result.match_method == "content_similarity"


def test_page_sized_anchor_is_preserved():
    result = match_document([region()], document([block("page", box(0, 0, 1, 1)), *fragments()]))[0]
    assert result.prediction_element_id == "page"


@pytest.mark.parametrize("reverse", [False, True])
def test_free_fragment_has_one_spatial_owner_independent_of_gt_list_order(reverse):
    gt = [region("A", box(0, 0, .6, 1)), region("B", box(.4, 0, 1, 1))]
    blocks = [block("left", box(0, 0, .4, .4)), block("right", box(.6, 0, 1, .4)),
              block("shared", box(.4, .6, .55, .65))]
    results = match_document(gt[::-1] if reverse else gt, document(blocks))
    all_ids = [eid for r in results for eid in r.context.get("source_element_ids", [r.prediction_element_id])]
    assert len(all_ids) == len(set(all_ids))
    assert results[0].context["source_element_ids"] == ["left", "shared"]
    assert results[1].prediction_element_id == "right"


def test_identical_duplicate_of_reserved_anchor_cannot_be_used_by_other_region():
    gt = [region("A", box(0, 0, .4, .35)), region("B")]
    blocks = [fragments()[0], block("alias", box(0, 0, .4, .35), "first"), fragments()[1]]
    results = match_document(gt, document(blocks))
    assert results[0].prediction["text"] == "first"
    assert results[1].prediction["text"] == "second"


def test_free_fragment_uses_greater_overlap_instead_of_first_gt():
    gt = [region("A", box(0, 0, .6, 1)), region("B", box(.4, 0, 1, 1))]
    blocks = [block("left", box(0, 0, .4, .4)), block("right", box(.6, 0, 1, .4)),
              block("shared", box(.45, .6, .65, .65))]
    results = match_document(gt, document(blocks))
    assert results[0].prediction_element_id == "left"
    assert results[1].context["source_element_ids"] == ["right", "shared"]


def test_configured_overlap_threshold_is_respected_for_completion():
    blocks = [*fragments(), block("partial", box(.2, .8, .5, .9))]
    strict = match_document([region()], document(blocks),
                            config=MatchingConfig(text_multiblock_min_overlap=.9))[0]
    default = match_document([region()], document(blocks))[0]
    assert strict.prediction["text"] == "first second"
    assert default.prediction["text"] == "first second partial"
