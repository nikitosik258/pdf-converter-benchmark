from __future__ import annotations

from collections import defaultdict

from pdf_benchmark.evaluation.aggregation import (
    aggregate_document_scores,
    aggregate_page_scores,
    aggregate_tool_scores,
)
from pdf_benchmark.evaluation.models import EvaluationReport, ObjectEvaluationResult
from pdf_benchmark.evaluation.scoring import CATEGORY_ORDER, ScoringConfig


def build_run_report(
    object_results: list[ObjectEvaluationResult],
    *,
    scoring_config: ScoringConfig,
) -> EvaluationReport:
    if not object_results:
        return EvaluationReport(
            metadata={
                "object_count": 0,
                "tools": [],
                "documents": [],
                "incomplete_tools": [],
            }
        )

    page_records = aggregate_page_scores(object_results)
    document_records = aggregate_document_scores(object_results)

    by_tool: dict[str, list[ObjectEvaluationResult]] = defaultdict(list)
    for result in object_results:
        by_tool[result.tool].append(result)

    tool_records = []
    incomplete: dict[str, list[str]] = {}
    for tool, results in sorted(by_tool.items()):
        present = {r.category for r in results}
        missing = [c for c in CATEGORY_ORDER if c not in present]

        # Category scores are still meaningful for a partial run. Disable strict
        # Overall, then remove the renormalized partial overall to avoid
        # presenting it as the benchmark Overall Quality Score.
        partial_cfg = scoring_config.model_copy(
            update={"strict_overall_categories": False}
        )
        records = aggregate_tool_scores(results, config=partial_cfg)
        if missing:
            incomplete[tool] = missing
            records = [
                r for r in records
                if r.score_name != "overall_quality_score"
            ]
        tool_records.extend(records)

    metric_records = [
        metric
        for result in object_results
        for metric in result.metrics
    ]
    return EvaluationReport(
        object_results=object_results,
        metric_records=metric_records,
        aggregate_scores=[*page_records, *document_records, *tool_records],
        metadata={
            "tools": sorted(by_tool),
            "documents": sorted({r.document_id for r in object_results}),
            "object_count": len(object_results),
            "aggregation_chain": "Object -> Document -> Category -> Tool",
            "incomplete_tools": incomplete,
            "overall_quality_emitted_only_for_complete_tools": True,
            "chemistry_reporting": {
                "detection_score": "all GT objects; document macro then subtype macro",
                "structured_extraction_score": "matched objects only; document macro then detected-subtype macro",
                "structured_extraction_without_detection": "N/A (aggregate record omitted)",
                "changes_chemistry_or_overall": False,
            },
        },
    )
