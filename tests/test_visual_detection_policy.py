from pdf_benchmark.benchmark.ground_truth import GroundTruthObject
from pdf_benchmark.benchmark.matching import MatchingConfig, match_document
from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.models import (
    BBox,
    ImageObject,
    Page,
    StandardizedDocument,
    ToolMetadata,
)


TARGET = BBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.4)
EXTRA = BBox(x_min=0.6, y_min=0.6, x_max=0.9, y_max=0.9)


def _gt():
    return GroundTruthObject(
        object_id="IMG_1",
        document_id="D1",
        page=1,
        object_type="image",
        bbox=TARGET,
        reference={},
    )


def _document():
    return StandardizedDocument(
        document_id="D1",
        source_pdf="x.pdf",
        tool=ToolMetadata(tool_name="fake", distribution_name="fake"),
        pages=[
            Page(
                page_number=1,
                width=100,
                height=100,
                images=[
                    ImageObject(
                        element_id="target",
                        page_number=1,
                        bbox=TARGET,
                        asset_path="target.png",
                        extraction_success=True,
                    ),
                    ImageObject(
                        element_id="unannotated",
                        page_number=1,
                        bbox=EXTRA,
                        asset_path="extra.png",
                        extraction_success=True,
                    ),
                ],
            )
        ],
    )


def _evaluate(match):
    return EvaluationFramework().evaluate_object(
        {
            "tool": "fake",
            "document_id": "D1",
            "page": 1,
            "object_id": match.object_id,
            "object_type": match.object_type,
            "reference": match.reference,
            "prediction": match.prediction,
            "context": match.context,
        }
    )


def test_sampled_visual_gt_does_not_label_unannotated_candidate_false_positive():
    match = match_document([_gt()], _document())[0]

    assert match.context["detection_policy"] == "sampled_gt_recall_v1"
    assert "detection_fp" not in match.context
    assert match.context["unmatched_candidate_count"] == 1

    result = _evaluate(match)
    metrics = {metric.metric_name: metric.raw_value for metric in result.metrics}
    assert metrics["detection_recall"] == 1.0
    assert "detection_f1" not in metrics
    assert result.object_score == 100.0
    assert result.details["detection_policy"] == "sampled_gt_recall_v1"


def test_exhaustive_visual_pages_keep_false_positive_f1_mode():
    config = MatchingConfig(visual_detection_policy="exhaustive_page_f1_v1")
    match = match_document([_gt()], _document(), config=config)[0]

    assert match.context["detection_fp"] == 1
    result = _evaluate(match)
    metrics = {metric.metric_name: metric.raw_value for metric in result.metrics}
    assert metrics["detection_f1"] == 2 / 3
    assert "detection_recall" not in metrics
    assert result.object_score < 100.0
    assert result.details["detection_policy"] == "exhaustive_page_f1_v1"
