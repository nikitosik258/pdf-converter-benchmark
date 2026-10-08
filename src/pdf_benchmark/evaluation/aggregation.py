from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import stdev
from typing import Callable, Iterable

from .models import (
    AggregateScoreRecord,
    EvaluationReport,
    MetricRecord,
    ObjectEvaluationResult,
)
from .scoring import CATEGORY_ORDER, ScoringConfig


def _clean_score(value: float) -> float:
    """Stabilize presentation-level floating point noise without changing metrics."""
    value = round(float(value), 12)
    if abs(value) < 1e-12:
        return 0.0
    if abs(value - 100.0) < 1e-12:
        return 100.0
    return value


def _avg(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        raise ValueError("Cannot average an empty collection")
    return _clean_score(sum(values) / len(values))


def _chemistry_balanced_mean(
    results: list[ObjectEvaluationResult],
) -> float:
    groups: dict[str, list[float]] = defaultdict(list)
    for result in results:
        groups[result.subtype or "unknown"].append(result.object_score)
    subgroup_means = [_avg(v) for v in groups.values()]
    return _avg(subgroup_means)


def _chemistry_balanced_values(
    results: list[ObjectEvaluationResult],
    value: Callable[[ObjectEvaluationResult], float],
) -> float:
    groups: dict[str, list[float]] = defaultdict(list)
    for result in results:
        groups[result.subtype or "unknown"].append(value(result))
    return _avg(_avg(values) for values in groups.values())


def _object_metric_value(
    result: ObjectEvaluationResult,
    metric_name: str,
) -> float | None:
    for metric in result.metrics:
        if metric.metric_name == metric_name:
            return metric.metric_value
    return None


def _chemistry_reporting_records(
    tool: str,
    results: list[ObjectEvaluationResult],
) -> list[AggregateScoreRecord]:
    """Publish chemistry coverage separately from conditional payload quality."""
    chemistry = [result for result in results if result.category == "chemistry"]
    if not chemistry:
        return []

    by_document: dict[str, list[ObjectEvaluationResult]] = defaultdict(list)
    for result in chemistry:
        by_document[result.document_id].append(result)

    detection_documents = [
        _chemistry_balanced_values(
            document_results,
            lambda result: 100.0 if result.matched else 0.0,
        )
        for document_results in by_document.values()
    ]
    records = [
        AggregateScoreRecord(
            tool=tool,
            scope="category",
            score_name="chemistry_detection_score",
            score=_avg(detection_documents),
            category="chemistry",
            n_objects=len(chemistry),
            n_documents=len(by_document),
            details={
                "aggregation": "document_macro_then_subtype_macro",
                "denominator": "all_chemistry_gt_objects",
                "matched_objects": sum(result.matched for result in chemistry),
                "conditional_on_detection": False,
                "included_in_chemistry_score": False,
                "included_in_overall": False,
            },
        )
    ]

    extraction_documents: list[float] = []
    extraction_object_count = 0
    extraction_subtypes: set[str] = set()
    for document_results in by_document.values():
        detected_with_quality = [
            result
            for result in document_results
            if result.matched
            and _object_metric_value(result, "structured_extraction") is not None
        ]
        if not detected_with_quality:
            continue
        extraction_object_count += len(detected_with_quality)
        extraction_subtypes.update(
            result.subtype or "unknown" for result in detected_with_quality
        )
        extraction_documents.append(
            _chemistry_balanced_values(
                detected_with_quality,
                lambda result: float(
                    _object_metric_value(result, "structured_extraction")
                ),
            )
        )

    # No detection means there is no payload whose quality can be measured.
    # Omit the record so storage/reporting represents the result as N/A.
    if extraction_documents:
        records.append(
            AggregateScoreRecord(
                tool=tool,
                scope="category",
                score_name="chemistry_structured_extraction_score",
                score=_avg(extraction_documents),
                category="chemistry",
                n_objects=extraction_object_count,
                n_documents=len(extraction_documents),
                details={
                    "aggregation": "detected_document_macro_then_detected_subtype_macro",
                    "denominator": "matched_chemistry_objects_only",
                    "conditional_on_detection": True,
                    "detected_subtypes": sorted(extraction_subtypes),
                    "included_in_chemistry_score": False,
                    "included_in_overall": False,
                },
            )
        )
    return records


def _category_object_mean(
    category: str,
    results: list[ObjectEvaluationResult],
) -> float:
    if not results:
        raise ValueError("Category aggregation requires at least one object")
    if category == "chemistry":
        # Macro average linear formulas vs structural formulas, so the four
        # structures do not automatically outweigh the two linear formulas.
        return _chemistry_balanced_mean(results)
    return _avg(r.object_score for r in results)


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    fraction = position - low
    return (
        sorted_values[low] * (1.0 - fraction)
        + sorted_values[high] * fraction
    )


def bootstrap_mean_ci(
    values: list[float],
    *,
    n_resamples: int = 10_000,
    seed: int = 42,
) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap_mean_ci requires values")
    if len(values) == 1:
        return values[0], values[0]

    rng = random.Random(seed)
    n = len(values)
    draws: list[float] = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        draws.append(_avg(sample))
    draws.sort()
    return _percentile(draws, 0.025), _percentile(draws, 0.975)


def aggregate_page_scores(
    results: list[ObjectEvaluationResult],
) -> list[AggregateScoreRecord]:
    grouped: dict[tuple[str, str, int, str], list[ObjectEvaluationResult]] = defaultdict(list)
    for r in results:
        grouped[(r.tool, r.document_id, r.page, r.category)].append(r)

    records: list[AggregateScoreRecord] = []
    page_category_scores: dict[tuple[str, str, int], dict[str, float]] = defaultdict(dict)

    for (tool, doc, page, category), objects in sorted(grouped.items()):
        score = _category_object_mean(category, objects)
        page_category_scores[(tool, doc, page)][category] = score
        records.append(
            AggregateScoreRecord(
                tool=tool,
                scope="page",
                score_name=f"{category}_page_score",
                score=score,
                document_id=doc,
                page=page,
                category=category,
                n_objects=len(objects),
            )
        )

    # Diagnostic page overall: average categories actually annotated on page.
    # It is NOT used in the final tool Overall Quality Score.
    for (tool, doc, page), categories in sorted(page_category_scores.items()):
        records.append(
            AggregateScoreRecord(
                tool=tool,
                scope="page",
                score_name="page_overall_present_categories",
                score=_avg(categories.values()),
                document_id=doc,
                page=page,
                n_objects=sum(
                    1
                    for r in results
                    if r.tool == tool
                    and r.document_id == doc
                    and r.page == page
                ),
                details={
                    "categories_present": sorted(categories),
                    "diagnostic_only": True,
                },
            )
        )
    return records


def aggregate_document_scores(
    results: list[ObjectEvaluationResult],
) -> list[AggregateScoreRecord]:
    grouped: dict[tuple[str, str, str], list[ObjectEvaluationResult]] = defaultdict(list)
    for r in results:
        grouped[(r.tool, r.document_id, r.category)].append(r)

    records: list[AggregateScoreRecord] = []
    doc_category_scores: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)

    for (tool, doc, category), objects in sorted(grouped.items()):
        score = _category_object_mean(category, objects)
        doc_category_scores[(tool, doc)][category] = score
        records.append(
            AggregateScoreRecord(
                tool=tool,
                scope="document",
                score_name=f"{category}_document_score",
                score=score,
                document_id=doc,
                category=category,
                n_objects=len(objects),
            )
        )

    # Diagnostic document overall across categories present in that document.
    for (tool, doc), categories in sorted(doc_category_scores.items()):
        records.append(
            AggregateScoreRecord(
                tool=tool,
                scope="document",
                score_name="document_overall_present_categories",
                score=_avg(categories.values()),
                document_id=doc,
                n_objects=sum(
                    1
                    for r in results
                    if r.tool == tool and r.document_id == doc
                ),
                details={
                    "categories_present": sorted(categories),
                    "diagnostic_only": True,
                },
            )
        )
    return records


def aggregate_tool_scores(
    results: list[ObjectEvaluationResult],
    *,
    config: ScoringConfig | None = None,
) -> list[AggregateScoreRecord]:
    cfg = config or ScoringConfig()
    documents = aggregate_document_scores(results)

    category_docs: dict[tuple[str, str], list[AggregateScoreRecord]] = defaultdict(list)
    for rec in documents:
        if rec.category is not None:
            category_docs[(rec.tool, rec.category)].append(rec)

    records: list[AggregateScoreRecord] = []
    tools = sorted({r.tool for r in results})

    for tool in tools:
        tool_category: dict[str, float] = {}

        for category in CATEGORY_ORDER:
            doc_recs = category_docs.get((tool, category), [])
            if not doc_recs:
                continue

            doc_values = [r.score for r in doc_recs]
            score = _avg(doc_values)
            tool_category[category] = score

            # Primary uncertainty is cluster bootstrap by document.
            if len(doc_values) >= 2:
                ci_low, ci_high = bootstrap_mean_ci(
                    doc_values,
                    n_resamples=cfg.bootstrap_resamples,
                    seed=cfg.bootstrap_seed,
                )
                sd = stdev(doc_values)
                ci_basis = "cluster_bootstrap_by_document"
            else:
                # Chemistry currently lives in one benchmark document.
                # Follow the Prompt-5 rule: object-level descriptive spread/CI,
                # explicitly marked as within-document, not cross-document.
                object_values = [
                    r.object_score
                    for r in results
                    if r.tool == tool and r.category == category
                ]
                ci_low, ci_high = bootstrap_mean_ci(
                    object_values,
                    n_resamples=cfg.bootstrap_resamples,
                    seed=cfg.bootstrap_seed,
                )
                sd = stdev(object_values) if len(object_values) >= 2 else None
                ci_basis = "object_bootstrap_within_single_document"

            records.append(
                AggregateScoreRecord(
                    tool=tool,
                    scope="category",
                    score_name=f"{category}_score",
                    score=score,
                    category=category,
                    n_objects=sum(
                        1
                        for r in results
                        if r.tool == tool and r.category == category
                    ),
                    n_documents=len(doc_values),
                    std_dev=sd,
                    ci95_low=ci_low,
                    ci95_high=ci_high,
                    ci_basis=ci_basis,
                    details={
                        "aggregation": "document_macro_average",
                    },
                )
            )

        records.extend(_chemistry_reporting_records(tool, results))

        missing = [c for c in CATEGORY_ORDER if c not in tool_category]
        if cfg.strict_overall_categories and missing:
            raise ValueError(
                f"Cannot calculate Overall Quality Score for {tool!r}; "
                f"missing category scores: {missing}. Unsupported required "
                "objects must be represented by zero-scored object results, "
                "not omitted."
            )

        available_weights = {
            c: w
            for c, w in cfg.overall_weights.items()
            if c in tool_category
        }
        weight_sum = sum(available_weights.values())
        overall = _clean_score(
            sum(tool_category[c] * w for c, w in available_weights.items())
            / weight_sum
        )

        records.append(
            AggregateScoreRecord(
                tool=tool,
                scope="tool",
                score_name="overall_quality_score",
                score=overall,
                n_objects=sum(1 for r in results if r.tool == tool),
                n_documents=len(
                    {r.document_id for r in results if r.tool == tool}
                ),
                details={
                    "weights": available_weights,
                    "category_scores": tool_category,
                    "equal_weights": all(
                        abs(cfg.overall_weights[c] - 1 / 6) < 1e-12
                        for c in CATEGORY_ORDER
                    ),
                },
            )
        )

    return records


def aggregate_benchmark(
    results: list[ObjectEvaluationResult],
    *,
    config: ScoringConfig | None = None,
) -> EvaluationReport:
    if not results:
        raise ValueError("Benchmark evaluation requires object results")

    page_records = aggregate_page_scores(results)
    document_records = aggregate_document_scores(results)
    tool_records = aggregate_tool_scores(results, config=config)

    metric_records: list[MetricRecord] = []
    for result in results:
        metric_records.extend(result.metrics)

    # "Whole benchmark" is a structured package over all tools. We do NOT
    # average competing tools into a single quality verdict because such a
    # number has no evaluation meaning.
    return EvaluationReport(
        object_results=results,
        metric_records=metric_records,
        aggregate_scores=[
            *page_records,
            *document_records,
            *tool_records,
        ],
        metadata={
            "tools": sorted({r.tool for r in results}),
            "documents": sorted({r.document_id for r in results}),
            "object_count": len(results),
            "aggregation_chain": "Object -> Document -> Category -> Tool",
            "page_scores_are_diagnostic": True,
            "chemistry_reporting": {
                "detection_score": "all GT objects; document macro then subtype macro",
                "structured_extraction_score": "matched objects only; document macro then detected-subtype macro",
                "structured_extraction_without_detection": "N/A (aggregate record omitted)",
                "changes_chemistry_or_overall": False,
            },
        },
    )
