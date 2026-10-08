import pytest

from pdf_benchmark.evaluation.aggregation import (
    aggregate_document_scores,
    aggregate_tool_scores,
    bootstrap_mean_ci,
)
from pdf_benchmark.evaluation.framework import EvaluationFramework
from pdf_benchmark.evaluation.models import ObjectEvaluationResult
from pdf_benchmark.evaluation.scoring import ScoringConfig


def obj(tool, doc, page, oid, category, score, subtype=None):
    object_type = {
        "text": "text",
        "table": "table",
        "math": "math_formula",
        "chemistry": "chemical_formula" if subtype == "linear_formula" else "chemical_structure",
        "image": "image",
        "diagram": "diagram",
    }[category]
    return ObjectEvaluationResult(
        tool=tool,
        document_id=doc,
        page=page,
        object_id=oid,
        object_type=object_type,
        category=category,
        subtype=subtype,
        matched=True,
        object_score=score,
        metrics=[],
    )


def test_document_macro_not_object_micro():
    # D1 has two text objects 100,100; D2 has one text object 0.
    # Tool category must be mean(document scores)=(100+0)/2=50,
    # not object micro average 66.67.
    results = [
        obj("t", "D1", 1, "a", "text", 100),
        obj("t", "D1", 1, "b", "text", 100),
        obj("t", "D2", 1, "c", "text", 0),
    ]
    # Add other required categories to make strict overall calculable.
    for category in ["table", "math", "image", "diagram"]:
        results.append(obj("t", "D1", 1, category, category, 50))
    results += [
        obj("t", "D1", 1, "cl", "chemistry", 50, "linear_formula"),
        obj("t", "D1", 1, "cs", "chemistry", 50, "structure"),
    ]

    recs = aggregate_tool_scores(
        results,
        config=ScoringConfig(
            bootstrap_resamples=200,
            strict_overall_categories=True,
        ),
    )
    text = next(r for r in recs if r.score_name == "text_score")
    assert text.score == pytest.approx(50.0)


def test_chemistry_subtypes_are_macro_balanced():
    # 2 linear objects at 100 and 4 structure objects at 0.
    # Balanced subtype score must be (100 + 0)/2 = 50, not 33.33.
    results = [
        obj("t", "D1", 1, "l1", "chemistry", 100, "linear_formula"),
        obj("t", "D1", 1, "l2", "chemistry", 100, "linear_formula"),
        obj("t", "D1", 1, "s1", "chemistry", 0, "structure"),
        obj("t", "D1", 1, "s2", "chemistry", 0, "structure"),
        obj("t", "D1", 1, "s3", "chemistry", 0, "structure"),
        obj("t", "D1", 1, "s4", "chemistry", 0, "structure"),
    ]
    doc_recs = aggregate_document_scores(results)
    chem = next(r for r in doc_recs if r.category == "chemistry")
    assert chem.score == pytest.approx(50.0)


def test_chemistry_reporting_separates_coverage_and_detected_payload_quality():
    framework = EvaluationFramework()
    common = {"tool": "t", "document_id": "D1", "page": 1}
    box = [0.1, 0.1, 0.5, 0.5]
    results = [
        framework.evaluate_object({
            **common, "object_id": "L1", "object_type": "chemical_formula",
            "reference": {"formula": "Fe3O4"}, "prediction": {"formula": "Fe3O4"},
        }),
        framework.evaluate_object({
            **common, "object_id": "L2", "object_type": "chemical_formula",
            "reference": {"formula": "FeSO4"}, "prediction": None,
        }),
        framework.evaluate_object({
            **common, "object_id": "S1", "object_type": "chemical_structure",
            "reference": {"bbox": box, "text_labels": ["OH"]},
            "prediction": {"bbox": box, "text_labels": ["OH"], "asset_path": "structure.svg"},
        }),
        framework.evaluate_object({
            **common, "object_id": "S2", "object_type": "chemical_structure",
            "reference": {"bbox": box, "text_labels": ["N=N"]}, "prediction": None,
        }),
    ]

    records = aggregate_tool_scores(
        results,
        config=ScoringConfig(bootstrap_resamples=200, strict_overall_categories=False),
    )
    detection = next(r for r in records if r.score_name == "chemistry_detection_score")
    extraction = next(
        r for r in records if r.score_name == "chemistry_structured_extraction_score"
    )

    assert detection.score == 50.0
    assert detection.n_objects == 4
    assert extraction.score == 100.0
    assert extraction.n_objects == 2
    assert extraction.details["conditional_on_detection"] is True
    assert next(r for r in records if r.score_name == "chemistry_score").score == 50.0


def test_chemistry_extraction_score_is_omitted_when_nothing_is_detected():
    framework = EvaluationFramework()
    result = framework.evaluate_object({
        "tool": "t", "document_id": "D1", "page": 1,
        "object_id": "L1", "object_type": "chemical_formula",
        "reference": {"formula": "Fe3O4"}, "prediction": None,
    })

    records = aggregate_tool_scores(
        [result],
        config=ScoringConfig(bootstrap_resamples=200, strict_overall_categories=False),
    )

    assert next(r for r in records if r.score_name == "chemistry_detection_score").score == 0
    assert not any(
        r.score_name == "chemistry_structured_extraction_score" for r in records
    )


def test_custom_overall_weights():
    results = [
        obj("t", "D1", 1, "text", "text", 100),
        obj("t", "D1", 1, "table", "table", 0),
        obj("t", "D1", 1, "math", "math", 0),
        obj("t", "D1", 1, "chem", "chemistry", 0, "linear_formula"),
        obj("t", "D1", 1, "img", "image", 0),
        obj("t", "D1", 1, "dia", "diagram", 0),
    ]
    cfg = ScoringConfig(
        overall_weights={
            "text": 0.5,
            "table": 0.1,
            "math": 0.1,
            "chemistry": 0.1,
            "image": 0.1,
            "diagram": 0.1,
        },
        bootstrap_resamples=200,
    )
    recs = aggregate_tool_scores(results, config=cfg)
    overall = next(r for r in recs if r.score_name == "overall_quality_score")
    assert overall.score == pytest.approx(50.0)


def test_bootstrap_reproducible_and_bounded():
    a = bootstrap_mean_ci([10, 20, 30], n_resamples=500, seed=42)
    b = bootstrap_mean_ci([10, 20, 30], n_resamples=500, seed=42)
    assert a == b
    assert 10 <= a[0] <= a[1] <= 30
