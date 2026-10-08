"""Descriptive statistics for the fixed PDF benchmark; no adapter dependencies."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean


def quantile(values, probability):
    values = sorted(values)
    if not values or not 0 <= probability <= 1:
        raise ValueError("Quantile requires observations and probability in [0, 1]")
    position = (len(values) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def document_category_means(objects):
    groups = defaultdict(list)
    for row in objects:
        groups[row["tool"], row["document_id"], row["category"]].append(row)
    result = {}
    for key, rows in groups.items():
        if key[2] == "chemistry":
            subtypes = defaultdict(list)
            for row in rows:
                subtypes[row["subtype"]].append(float(row["object_score"]))
            result[key] = mean(mean(values) for values in subtypes.values())
        else:
            result[key] = mean(float(row["object_score"]) for row in rows)
    return result


def tool_category_means(document_scores):
    groups = defaultdict(list)
    for (tool, _document, category), value in document_scores.items():
        groups[tool, category].append(value)
    return {key: mean(values) for key, values in groups.items()}


def chemistry_reporting_scores(objects, metrics):
    """Reproduce detection and conditional extraction diagnostics independently."""
    detection_groups = defaultdict(list)
    for row in objects:
        if row["category"] != "chemistry":
            continue
        matched = row["matched"]
        if isinstance(matched, str):
            matched = matched.casefold() == "true"
        detection_groups[
            row["tool"], row["document_id"], row.get("subtype") or "unknown"
        ].append(100.0 if matched else 0.0)

    detection_documents = defaultdict(list)
    for (tool, document, _subtype), values in detection_groups.items():
        detection_documents[tool, document].append(mean(values))

    extraction_groups = defaultdict(list)
    for row in metrics:
        if (
            row.get("category") == "chemistry"
            and row.get("metric_name") == "structured_extraction"
        ):
            extraction_groups[
                row["tool"],
                row["document_id"],
                row.get("subtype") or "unknown",
            ].append(float(row["metric_value"]))

    extraction_documents = defaultdict(list)
    for (tool, document, _subtype), values in extraction_groups.items():
        extraction_documents[tool, document].append(mean(values))

    tools = sorted({key[0] for key in detection_documents})
    return {
        tool: {
            "chemistry_detection_score": mean(
                mean(subtype_values)
                for (current_tool, _document), subtype_values
                in detection_documents.items()
                if current_tool == tool
            ),
            "chemistry_structured_extraction_score": (
                mean(
                    mean(subtype_values)
                    for (current_tool, _document), subtype_values
                    in extraction_documents.items()
                    if current_tool == tool
                )
                if any(current_tool == tool for current_tool, _ in extraction_documents)
                else None
            ),
        }
        for tool in tools
    }


def paired_bootstrap(left, right, n_resamples=10000, seed=42):
    if not left or len(left) != len(right):
        raise ValueError("Paired bootstrap requires equal nonempty samples")
    differences = [a - b for a, b in zip(left, right)]
    if len(differences) < 2:
        return mean(differences), None, None
    rng = random.Random(seed)
    n = len(differences)
    draws = [sum(differences[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_resamples)]
    return mean(differences), quantile(draws, .025), quantile(draws, .975)


def variance_components(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["document_id"]].append(float(row["object_score"]))
    values = [v for group in groups.values() for v in group]
    grand = mean(values)
    within = sum(sum((v - mean(group)) ** 2 for v in group) for group in groups.values())
    between = sum(len(group) * (mean(group) - grand) ** 2 for group in groups.values())
    total = sum((v - grand) ** 2 for v in values)
    return {"ss_total": total, "ss_within_document": within, "ss_between_documents": between,
            "between_document_share": between / total if total > 1e-12 else None,
            "sample_variance": total / (len(values) - 1) if len(values) > 1 else None}


def pareto_tools(rows, quality="overall_score", cost="sec_per_page", tolerance=1e-9):
    """Higher quality and lower cost; retain exact ties on the frontier."""
    return {a["tool"] for a in rows if not any(
        b[quality] >= a[quality] - tolerance and b[cost] <= a[cost] + tolerance
        and (b[quality] > a[quality] + tolerance or b[cost] < a[cost] - tolerance)
        for b in rows
    )}


def average_ranks(values):
    ranks = [0.] * len(values)
    ordered = sorted(range(len(values)), key=lambda i: values[i])
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        for index in ordered[start:end]:
            ranks[index] = (start + 1 + end) / 2
        start = end
    return ranks


def spearman(left, right):
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Spearman requires equal samples of at least two observations")
    x, y = average_ranks(left), average_ranks(right)
    mx, my = mean(x), mean(y)
    denominator = math.sqrt(sum((v-mx)**2 for v in x) * sum((v-my)**2 for v in y))
    return sum((a-mx)*(b-my) for a, b in zip(x, y)) / denominator if denominator else None
