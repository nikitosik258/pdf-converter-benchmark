import pytest

from scripts.analysis_statistics import (
    chemistry_reporting_scores, document_category_means, paired_bootstrap, pareto_tools, spearman,
    tool_category_means, variance_components,
)


def row(doc, score, category="text", subtype=None):
    return {"tool": "tool", "document_id": doc, "category": category,
            "subtype": subtype, "object_score": score}


def test_document_macro_does_not_overweight_more_objects():
    objects = [row("A", 100), row("B", 0), row("B", 0), row("B", 0)]
    assert tool_category_means(document_category_means(objects))["tool", "text"] == 50


def test_chemistry_balances_subtypes_and_omits_unannotated_documents():
    objects = [row("A", 100, "chemistry", "linear_formula")] * 2
    objects += [row("A", 0, "chemistry", "structure")] * 4
    objects += [row("B", 90)]
    scores = document_category_means(objects)
    assert scores["tool", "A", "chemistry"] == 50
    assert ("tool", "B", "chemistry") not in scores


def test_chemistry_reporting_separates_detection_from_conditional_extraction():
    objects = [
        {**row("A", 100, "chemistry", "linear_formula"), "object_id": "L1", "matched": True},
        {**row("A", 0, "chemistry", "linear_formula"), "object_id": "L2", "matched": False},
        {**row("A", 100, "chemistry", "structure"), "object_id": "S1", "matched": True},
        {**row("A", 0, "chemistry", "structure"), "object_id": "S2", "matched": False},
    ]
    metrics = [
        {"tool": "tool", "document_id": "A", "category": "chemistry",
         "subtype": "linear_formula", "metric_name": "structured_extraction", "metric_value": 80},
        {"tool": "tool", "document_id": "A", "category": "chemistry",
         "subtype": "structure", "metric_name": "structured_extraction", "metric_value": 60},
    ]

    scores = chemistry_reporting_scores(objects, metrics)["tool"]

    assert scores["chemistry_detection_score"] == 50
    assert scores["chemistry_structured_extraction_score"] == 70


def test_chemistry_structured_extraction_is_na_without_detections():
    objects = [
        {**row("A", 0, "chemistry", "linear_formula"), "object_id": "L1", "matched": False},
        {**row("A", 0, "chemistry", "structure"), "object_id": "S1", "matched": False},
    ]

    scores = chemistry_reporting_scores(objects, [])["tool"]

    assert scores["chemistry_detection_score"] == 0
    assert scores["chemistry_structured_extraction_score"] is None


def test_paired_bootstrap_preserves_pairing_and_requires_multiple_clusters_for_ci():
    # Large marginal variance, but every within-document difference is 5.
    assert paired_bootstrap([5, 55, 105], [0, 50, 100], n_resamples=100) == (5, 5, 5)
    assert paired_bootstrap([5], [0]) == (5, None, None)
    with pytest.raises(ValueError):
        paired_bootstrap([1, 2], [1])


def test_variance_decomposition_uses_document_sizes():
    result = variance_components([row("A", 0), row("A", 10), row("B", 100)])
    assert result["ss_total"] == pytest.approx(result["ss_within_document"] + result["ss_between_documents"])
    assert result["ss_within_document"] == 50
    assert variance_components([row("A", 0), row("B", 0)])["between_document_share"] is None


def test_pareto_keeps_tradeoffs_and_ties_but_drops_dominated_tools():
    rows = [dict(tool=t, overall_score=q, sec_per_page=s) for t, q, s in
            [("fast", 20, 1), ("quality", 40, 3), ("dominated", 10, 2), ("tie", 20, 1)]]
    assert pareto_tools(rows) == {"fast", "quality", "tie"}


def test_spearman_handles_ties_and_undefined_zero_cost_axis():
    assert spearman([1, 1, 3], [2, 2, 4]) == pytest.approx(1)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1)
    assert spearman([0, 0, 0], [1, 2, 3]) is None
