import csv
from pathlib import Path

import pytest

from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.evaluation.models import EvaluationObjectInput
from pdf_benchmark.evaluation.scoring import ScoringConfig
from pdf_benchmark.evaluation.storage import save_evaluation_report


def test_framework_object_result_long_metric_shape():
    fw = EvaluationFramework()
    result = fw.evaluate_object(
        EvaluationObjectInput(
            tool="tool_a",
            document_id="D01",
            page=1,
            object_id="TXT_001",
            object_type="text",
            reference={"text": "abc"},
            prediction={"text": "axc"},
        )
    )
    assert result.category == "text"
    assert result.object_score < 100
    names = {m.metric_name for m in result.metrics}
    assert {"cer", "wer", "edit_similarity"} <= names
    for metric in result.metrics:
        assert metric.tool == "tool_a"
        assert metric.document_id == "D01"
        assert metric.page == 1
        assert metric.object_id == "TXT_001"
        assert 0 <= metric.metric_value <= 100


def _perfect(tool, doc, page, oid, otype, reference, prediction):
    return EvaluationFramework(
        ScoringConfig(
            bootstrap_resamples=200,
            strict_overall_categories=True,
        )
    ).evaluate_object(
        {
            "tool": tool,
            "document_id": doc,
            "page": page,
            "object_id": oid,
            "object_type": otype,
            "reference": reference,
            "prediction": prediction,
        }
    )


def test_full_tool_aggregation_equal_weights_is_100(tmp_path):
    tool = "perfect_tool"
    results = [
        _perfect(tool, "D1", 1, "TXT_1", "text", {"text": "abc"}, {"text": "abc"}),
        _perfect(
            tool, "D1", 1, "TAB_1", "table",
            {
                "element_id": "t", "page_number": 1, "rows": 1, "columns": 1,
                "cells": [{"row_index": 0, "column_index": 0, "text": "A"}],
            },
            {
                "element_id": "t2", "page_number": 1, "rows": 1, "columns": 1,
                "cells": [{"row_index": 0, "column_index": 0, "text": "A"}],
            },
        ),
        _perfect(tool, "D2", 1, "MATH_1", "math_formula", {"latex": "x^2"}, {"latex": "x^2"}),
        _perfect(tool, "D2", 1, "CHEM_1", "chemical_formula", {"formula": "Fe₃O₄"}, {"formula": "Fe3O4"}),
        _perfect(
            tool, "D3", 1, "IMG_1", "image",
            {"bbox": [0.1, 0.1, 0.5, 0.5], "caption": "Рис. 1"},
            {"bbox": [0.1, 0.1, 0.5, 0.5], "caption": "Рис. 1", "extraction_success": True},
        ),
        _perfect(
            tool, "D3", 1, "DIA_1", "diagram",
            {
                "bbox": [0.1, 0.1, 0.5, 0.5],
                "caption": "Рис. 2",
                "text_elements": ["Input"],
                "key_elements": ["Input"],
                "subfigures": [{"label": "a"}],
            },
            {
                "bbox": [0.1, 0.1, 0.5, 0.5],
                "caption": "Рис. 2",
                "text_elements": ["Input"],
                "key_elements": ["Input"],
                "subfigures": [{"label": "a"}],
            },
        ),
    ]

    fw = EvaluationFramework(
        ScoringConfig(
            bootstrap_resamples=200,
            strict_overall_categories=True,
        )
    )
    report = fw.evaluate_benchmark(results)

    overall = [
        x for x in report.aggregate_scores
        if x.scope == "tool" and x.score_name == "overall_quality_score"
    ]
    assert len(overall) == 1
    assert overall[0].score == 100.0

    paths = save_evaluation_report(report, tmp_path / "metrics")
    for path in paths.values():
        assert Path(path).exists()

    with Path(paths["metric_records_csv"]).open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert rows
    assert {"tool", "document_id", "page", "object_id", "object_type", "metric_name", "metric_value"} <= set(rows[0])


def test_missing_required_category_is_rejected():
    fw = EvaluationFramework(
        ScoringConfig(
            bootstrap_resamples=200,
            strict_overall_categories=True,
        )
    )
    result = fw.evaluate_object(
        {
            "tool": "x",
            "document_id": "D1",
            "page": 1,
            "object_id": "TXT",
            "object_type": "text",
            "reference": {"text": "abc"},
            "prediction": {"text": "abc"},
        }
    )
    with pytest.raises(ValueError, match="missing category scores"):
        fw.evaluate_tool([result])


def test_unsupported_object_is_not_na():
    fw = EvaluationFramework()
    result = fw.evaluate_object(
        {
            "tool": "no_math_tool",
            "document_id": "D1",
            "page": 1,
            "object_id": "MATH",
            "object_type": "math_formula",
            "reference": {"latex": "x"},
            "prediction": None,
        }
    )
    assert result.matched is False
    assert result.object_score == 0.0


def test_math_framework_scores_raw_and_normalized_payloads_separately():
    result = EvaluationFramework().evaluate_object(
        {
            "tool": "math_tool",
            "document_id": "D1",
            "page": 1,
            "object_id": "MATH_1",
            "object_type": "math_formula",
            "reference": {"latex": r"\frac{x}{y}"},
            "prediction": {
                "latex": r"\dfrac{x}{y}",
                "normalized_latex": r"\frac{x}{y}",
            },
        }
    )
    metrics = {metric.metric_name: metric.raw_value for metric in result.metrics}
    assert metrics["raw_exact_match"] == 0.0
    assert metrics["normalized_exact_match"] == 1.0
    assert result.object_score == pytest.approx(95.0)
