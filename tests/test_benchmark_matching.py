from pdf_benchmark.benchmark.ground_truth import GroundTruthObject
from pdf_benchmark.benchmark.matching import MatchingConfig, match_document
from pdf_benchmark.models import (
    BBox,
    Formula,
    Page,
    StandardizedDocument,
    TextBlock,
    ToolMetadata,
)


def test_math_matching_preserves_raw_and_normalized_representations():
    gt = [
        GroundTruthObject(
            object_id="MATH_1",
            document_id="D1",
            page=1,
            object_type="math_formula",
            reference={"latex": r"\frac{x}{y}"},
        )
    ]
    prediction = StandardizedDocument(
        document_id="D1",
        source_pdf="x.pdf",
        tool=ToolMetadata(tool_name="fake", distribution_name="fake"),
        pages=[
            Page(
                page_number=1,
                width=100,
                height=100,
                formulas=[
                    Formula(
                        element_id="f1",
                        page_number=1,
                        latex=r"\dfrac{x}{y}",
                        normalized_latex=r"\frac{x}{y}",
                    )
                ],
            )
        ],
    )

    match = match_document(gt, prediction)[0]

    assert match.matched
    assert match.prediction["latex"] == r"\dfrac{x}{y}"
    assert match.prediction["normalized_latex"] == r"\frac{x}{y}"


def doc(blocks):
    return StandardizedDocument(
        document_id="D1",
        source_pdf="x.pdf",
        tool=ToolMetadata(tool_name="fake", distribution_name="fake"),
        pages=[Page(page_number=1, width=100, height=100, text_blocks=blocks)],
    )


def test_spatial_hungarian_is_one_to_one():
    gt = [
        GroundTruthObject(
            object_id="A",
            document_id="D1",
            page=1,
            object_type="text",
            bbox=BBox(x_min=0.0, y_min=0.0, x_max=0.4, y_max=0.4),
            reference={"text": "left"},
        ),
        GroundTruthObject(
            object_id="B",
            document_id="D1",
            page=1,
            object_type="text",
            bbox=BBox(x_min=0.6, y_min=0.0, x_max=1.0, y_max=0.4),
            reference={"text": "right"},
        ),
    ]
    prediction = doc([
        TextBlock(
            element_id="right",
            page_number=1,
            bbox=BBox(x_min=0.6, y_min=0.0, x_max=1.0, y_max=0.4),
            raw_text="right",
        ),
        TextBlock(
            element_id="left",
            page_number=1,
            bbox=BBox(x_min=0.0, y_min=0.0, x_max=0.4, y_max=0.4),
            raw_text="left",
        ),
    ])
    matches = match_document(gt, prediction)
    assert {m.object_id: m.prediction_element_id for m in matches} == {
        "A": "left",
        "B": "right",
    }


def test_content_fallback_when_bbox_missing():
    gt = [
        GroundTruthObject(
            object_id="A",
            document_id="D1",
            page=1,
            object_type="text",
            reference={"text": "точный текст"},
        ),
    ]
    prediction = doc([
        TextBlock(element_id="wrong", page_number=1, raw_text="другое"),
        TextBlock(element_id="right", page_number=1, raw_text="точный текст"),
    ])
    match = match_document(
        gt,
        prediction,
        config=MatchingConfig(content_min_similarity=0.5),
    )[0]
    assert match.matched
    assert match.prediction_element_id == "right"
    assert match.match_method == "content_similarity"


def test_unmatched_prediction_none():
    gt = [
        GroundTruthObject(
            object_id="A",
            document_id="D1",
            page=1,
            object_type="text",
            reference={"text": "abc"},
        ),
    ]
    prediction = doc([
        TextBlock(element_id="x", page_number=1, raw_text="zzzzzzzz"),
    ])
    match = match_document(
        gt,
        prediction,
        config=MatchingConfig(content_min_similarity=0.8),
    )[0]
    assert not match.matched
    assert match.prediction is None


def _text_region(object_id="A", x_min=0.0, x_max=1.0):
    return GroundTruthObject(
        object_id=object_id,
        document_id="D1",
        page=1,
        object_type="text",
        bbox=BBox(x_min=x_min, y_min=0.0, x_max=x_max, y_max=1.0),
        reference={"text": "same word same"},
    )


def _word(element_id, text, x):
    return TextBlock(
        element_id=element_id,
        page_number=1,
        bbox=BBox(x_min=x, y_min=0.1, x_max=x + 0.04, y_max=0.12),
        raw_text=text,
    )


def test_multiblock_preserves_repeated_words_in_reading_order():
    prediction = doc([
        _word("last", "same", 0.3),
        _word("middle", "word", 0.2),
        _word("first", "same", 0.1),
    ])
    prediction.pages[0].reading_order = ["first", "middle", "last"]

    match = match_document([_text_region()], prediction)[0]

    assert match.match_method == "text_multiblock_overlap"
    assert match.prediction["text"].split() == ["same", "word", "same"]
    assert match.context["source_element_ids"] == ["first", "middle", "last"]
    assert match.context["source_block_count"] == 3


def test_multiblock_deduplicates_identical_text_at_identical_bbox():
    prediction = doc([
        _word("original", "same", 0.1),
        _word("duplicate", "same", 0.1),
        _word("repeat", "same", 0.3),
    ])

    match = match_document([_text_region()], prediction)[0]

    assert match.match_method == "text_multiblock_overlap"
    assert match.prediction["text"].split() == ["same", "same"]
    assert match.context["source_element_ids"] == ["original", "repeat"]


def test_multiblock_preserves_different_text_at_identical_bbox():
    prediction = doc([
        _word("first", "same", 0.1),
        _word("second", "word", 0.1),
    ])

    match = match_document([_text_region()], prediction)[0]

    assert match.match_method == "text_multiblock_overlap"
    assert match.prediction["text"].split() == ["same", "word"]


def test_multiblock_does_not_reuse_words_between_gt_regions():
    prediction = doc([
        _word("first", "same", 0.1),
        _word("second", "same", 0.3),
        _word("outside", "outside", 0.8),
    ])
    # Both GT boxes include the same two words. Only one may consume them.
    gt = [_text_region("A", x_max=0.6), _text_region("B", x_max=0.6)]

    matches = match_document(gt, prediction)

    assert matches[0].prediction["text"].split() == ["same", "same"]
    assert matches[0].context["source_element_ids"] == ["first", "second"]
    assert not matches[1].matched
    assert matches[1].prediction is None
