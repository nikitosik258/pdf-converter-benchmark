from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from typing import TypeVar

from pdf_benchmark.models import BBox

T = TypeVar("T", bound=Hashable)


def clamp01(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"Metric is not finite: {value!r}")
    return min(1.0, max(0.0, float(value)))


def score100(value_01: float) -> float:
    return 100.0 * clamp01(value_01)


def mean(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        raise ValueError("mean() requires at least one value")
    return sum(values) / len(values)


def weighted_mean_available(
    values: dict[str, float | None],
    weights: dict[str, float],
) -> float:
    """Weighted mean over metrics that are applicable/available.

    Weights are re-normalized only when Ground Truth lacks a metric entirely
    (for example a diagram without a caption annotation). A recognition miss is
    represented as value=0.0, NOT None, so it is still penalized.
    """
    numerator = 0.0
    denominator = 0.0
    for name, weight in weights.items():
        value = values.get(name)
        if value is None:
            continue
        if weight < 0:
            raise ValueError("Metric weights must be non-negative")
        numerator += float(value) * float(weight)
        denominator += float(weight)

    if denominator <= 0:
        raise ValueError("No applicable metrics are available for scoring")
    return numerator / denominator


def levenshtein_distance(a: Sequence, b: Sequence) -> int:
    """Memory-efficient Levenshtein edit distance.

    Works for strings and token sequences. Unit insertion/deletion/substitution.
    """
    if a == b:
        return 0
    if len(a) == 0:
        return len(b)
    if len(b) == 0:
        return len(a)

    # Keep the inner DP row short.
    if len(a) < len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (0 if item_a == item_b else 1)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def error_rate(reference: Sequence, prediction: Sequence) -> float:
    """Edit error rate: (S + D + I) / N_reference.

    If reference is empty:
      - empty prediction => 0.0 (perfect)
      - non-empty prediction => 1.0 (all output is spurious)

    For non-empty references the rate may exceed 1.0 when insertions dominate.
    This raw value is intentionally preserved; quality score clamps 1-error_rate
    to [0,1].
    """
    if len(reference) == 0:
        return 0.0 if len(prediction) == 0 else 1.0
    return levenshtein_distance(reference, prediction) / len(reference)


def error_rate_score(rate: float) -> float:
    return clamp01(1.0 - float(rate))


def normalized_edit_similarity(reference: Sequence, prediction: Sequence) -> float:
    """1 - Levenshtein/max(lengths), bounded to [0,1]."""
    denom = max(len(reference), len(prediction))
    if denom == 0:
        return 1.0
    return clamp01(1.0 - levenshtein_distance(reference, prediction) / denom)


def word_tokens(text: str) -> list[str]:
    # WER uses whitespace-separated word units after normalization. Keeping
    # punctuation attached makes punctuation-related OCR errors measurable.
    return text.split()


def precision_recall_f1_from_counts(
    tp: int,
    fp: int,
    fn: int,
) -> tuple[float, float, float]:
    """Precision/Recall/F1 with explicit empty-set conventions.

    - tp=fp=fn=0: perfect agreement => P=R=F1=1
    - no predictions but positives exist: P=1, R=0, F1=0
      (there are no false positive predictions; recall captures the miss)
    - predictions exist but GT has no positives: P=0, R=1, F1=0
      (there are no positives to miss; precision captures spurious output)

    These conventions keep F1 intuitive and avoid division-by-zero.
    """
    for name, value in {"tp": tp, "fp": fp, "fn": fn}.items():
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    if tp == 0 and fp == 0 and fn == 0:
        return 1.0, 1.0, 1.0

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return precision, recall, f1


def multiset_precision_recall_f1(
    reference: Iterable[T],
    prediction: Iterable[T],
) -> tuple[float, float, float]:
    """Precision/Recall/F1 preserving repeated tokens/items."""
    ref = Counter(reference)
    pred = Counter(prediction)
    tp = sum((ref & pred).values())
    fp = sum((pred - ref).values())
    fn = sum((ref - pred).values())
    return precision_recall_f1_from_counts(tp, fp, fn)


def set_precision_recall_f1(
    reference: Iterable[T],
    prediction: Iterable[T],
) -> tuple[float, float, float]:
    ref = set(reference)
    pred = set(prediction)
    tp = len(ref & pred)
    fp = len(pred - ref)
    fn = len(ref - pred)
    return precision_recall_f1_from_counts(tp, fp, fn)


def bbox_iou(reference: BBox | None, prediction: BBox | None) -> float:
    if reference is None or prediction is None:
        return 0.0

    x_left = max(reference.x_min, prediction.x_min)
    y_top = max(reference.y_min, prediction.y_min)
    x_right = min(reference.x_max, prediction.x_max)
    y_bottom = min(reference.y_max, prediction.y_max)

    inter_w = max(0.0, x_right - x_left)
    inter_h = max(0.0, y_bottom - y_top)
    intersection = inter_w * inter_h

    ref_area = max(0.0, reference.x_max - reference.x_min) * max(
        0.0, reference.y_max - reference.y_min
    )
    pred_area = max(0.0, prediction.x_max - prediction.x_min) * max(
        0.0, prediction.y_max - prediction.y_min
    )
    union = ref_area + pred_area - intersection
    if union <= 0:
        return 0.0
    return clamp01(intersection / union)


def reading_order_score(
    reference_order: Sequence[str],
    prediction_order: Sequence[str],
) -> tuple[float, float, float]:
    """Return (coverage, pair_order, final_score).

    coverage = fraction of GT blocks present in prediction.
    pair_order = fraction of GT block pairs that keep the correct relative
                 order among blocks that are present.
    final = coverage * pair_order.

    Duplicate IDs are rejected because relative order would be ambiguous.
    """
    if len(set(reference_order)) != len(reference_order):
        raise ValueError("reference_order contains duplicate IDs")
    if len(set(prediction_order)) != len(prediction_order):
        raise ValueError("prediction_order contains duplicate IDs")

    if not reference_order:
        if not prediction_order:
            return 1.0, 1.0, 1.0
        return 0.0, 1.0, 0.0

    pred_pos = {item: idx for idx, item in enumerate(prediction_order)}
    present = [item for item in reference_order if item in pred_pos]
    coverage = len(present) / len(reference_order)

    if len(present) < 2:
        pair_order = 1.0
    else:
        correct = 0
        total = 0
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                total += 1
                if pred_pos[present[i]] < pred_pos[present[j]]:
                    correct += 1
        pair_order = correct / total if total else 1.0

    return coverage, pair_order, coverage * pair_order


_LATEX_TOKEN_RE = re.compile(
    r"""
    \\[A-Za-z]+      |  # command
    \\[^A-Za-z\s]    |  # escaped punctuation
    [A-Za-z]+        |  # identifiers
    \d+(?:\.\d+)?    |  # numbers
    [\{\}\[\]\(\)_\^]|
    [+\-*/=<>.,;:|]  |
    \S
    """,
    re.VERBOSE,
)


def latex_tokens(text: str) -> list[str]:
    return _LATEX_TOKEN_RE.findall(text)


_CHEM_TOKEN_RE = re.compile(
    r"[A-Z][a-z]?|\d+|\^|[+\-]|[()\[\]{}]|·|\.|[A-Za-z]+|\S"
)


def chemistry_tokens(text: str) -> list[str]:
    return _CHEM_TOKEN_RE.findall(text)
