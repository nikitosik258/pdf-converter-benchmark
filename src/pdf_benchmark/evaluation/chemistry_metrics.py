from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from pdf_benchmark.models import BBox
from pdf_benchmark.normalization.chemistry import normalize_chemical_formula
from pdf_benchmark.normalization.text import normalize_text

from .common import (
    bbox_iou,
    chemistry_tokens,
    multiset_precision_recall_f1,
    normalized_edit_similarity,
    weighted_mean_available,
)


CHEM_LINEAR_WEIGHTS = {
    "normalized_exact_match": 0.35,
    "token_f1": 0.30,
    "edit_similarity": 0.20,
    "composition_similarity": 0.15,
}

CHEM_STRUCTURE_WEIGHTS = {
    "detection": 0.30,
    "iou": 0.25,
    "label_f1": 0.25,
    "extraction": 0.20,
}

CHEM_STRUCTURE_EXTRACTION_WEIGHTS = {
    "iou": CHEM_STRUCTURE_WEIGHTS["iou"],
    "label_f1": CHEM_STRUCTURE_WEIGHTS["label_f1"],
    "extraction": CHEM_STRUCTURE_WEIGHTS["extraction"],
}


def _strip_charge(formula: str) -> str:
    return re.sub(r"\^[0-9]*[+-]$", "", formula)


def _read_number(text: str, index: int) -> tuple[int, int]:
    start = index
    while index < len(text) and text[index].isdigit():
        index += 1
    if index == start:
        return 1, index
    return int(text[start:index]), index


def _parse_group(
    text: str,
    index: int,
    closing: str | None = None,
) -> tuple[Counter[str], int] | None:
    counts: Counter[str] = Counter()
    pairs = {"(": ")", "[": "]", "{": "}"}

    while index < len(text):
        ch = text[index]

        if closing is not None and ch == closing:
            return counts, index + 1

        if ch in pairs:
            nested = _parse_group(text, index + 1, pairs[ch])
            if nested is None:
                return None
            nested_counts, index = nested
            multiplier, index = _read_number(text, index)
            for element, count in nested_counts.items():
                counts[element] += count * multiplier
            continue

        if ch.isupper():
            element = ch
            index += 1
            if index < len(text) and text[index].islower():
                element += text[index]
                index += 1
            multiplier, index = _read_number(text, index)
            counts[element] += multiplier
            continue

        return None

    if closing is not None:
        return None
    return counts, index


def parse_chemical_composition(
    formula: str,
) -> Counter[str] | None:
    """Parse common linear molecular formulas into elemental counts.

    Supported:
    - element symbols and integer subscripts;
    - (), [], {} grouping with multipliers;
    - hydrate/addition separator '·';
    - leading segment coefficient, e.g. 6H2O;
    - terminal normalized charge ^2-/^+ (ignored for atom composition).

    Unsupported syntax returns None rather than guessing.
    """
    text = normalize_chemical_formula(formula)
    text = _strip_charge(text)
    if not text:
        return Counter()

    total: Counter[str] = Counter()
    for segment in text.split("·"):
        if not segment:
            return None
        coefficient, idx = _read_number(segment, 0)
        parsed = _parse_group(segment, idx)
        if parsed is None:
            return None
        counts, end = parsed
        if end != len(segment):
            return None
        for element, count in counts.items():
            total[element] += coefficient * count
    return total


def composition_similarity(
    reference_formula: str,
    prediction_formula: str,
) -> float:
    ref = parse_chemical_composition(reference_formula)
    pred = parse_chemical_composition(prediction_formula)
    if ref is None or pred is None:
        return 0.0

    elements = set(ref) | set(pred)
    if not elements:
        return 1.0

    difference = sum(abs(ref[e] - pred[e]) for e in elements)
    denominator = sum(ref[e] + pred[e] for e in elements)
    if denominator == 0:
        return 1.0
    return max(0.0, 1.0 - difference / denominator)


def calculate_linear_chemistry_metrics(
    reference_formula: str,
    prediction_formula: str,
) -> dict[str, float]:
    ref = normalize_chemical_formula(reference_formula)
    pred = normalize_chemical_formula(prediction_formula)

    exact = 1.0 if ref == pred else 0.0
    token_precision, token_recall, token_f1 = multiset_precision_recall_f1(
        chemistry_tokens(ref),
        chemistry_tokens(pred),
    )
    edit_sim = normalized_edit_similarity(ref, pred)
    comp_sim = composition_similarity(ref, pred)

    score = weighted_mean_available(
        {
            "normalized_exact_match": exact,
            "token_f1": token_f1,
            "edit_similarity": edit_sim,
            "composition_similarity": comp_sim,
        },
        CHEM_LINEAR_WEIGHTS,
    )

    return {
        "normalized_exact_match": exact,
        "token_precision": token_precision,
        "token_recall": token_recall,
        "token_f1": token_f1,
        "edit_similarity": edit_sim,
        "composition_similarity": comp_sim,
        "chem_linear_score": score,
    }


def _label_tokens(values: Iterable[str]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        normalized = normalize_text(value, preserve_paragraphs=False)
        tokens.extend(normalized.split())
    return tokens


def calculate_structure_chemistry_metrics(
    *,
    reference_bbox: BBox | None,
    prediction_bbox: BBox | None,
    reference_labels: list[str],
    prediction_labels: list[str],
    prediction_exists: bool,
    extraction_success: bool,
) -> dict[str, float | None]:
    detection = 1.0 if prediction_exists else 0.0
    iou = bbox_iou(reference_bbox, prediction_bbox) if prediction_exists else 0.0

    label_precision, label_recall, label_f1 = multiset_precision_recall_f1(
        _label_tokens(reference_labels),
        _label_tokens(prediction_labels if prediction_exists else []),
    )
    extraction = 1.0 if (prediction_exists and extraction_success) else 0.0

    # Conditional quality of the structured payload. Detection coverage is
    # published separately; a missing prediction has no extraction-quality
    # observation instead of receiving a second zero for the same miss.
    structured_extraction = (
        weighted_mean_available(
            {
                "iou": iou,
                "label_f1": label_f1,
                "extraction": extraction,
            },
            CHEM_STRUCTURE_EXTRACTION_WEIGHTS,
        )
        if prediction_exists
        else None
    )

    score = weighted_mean_available(
        {
            "detection": detection,
            "iou": iou,
            "label_f1": label_f1,
            "extraction": extraction,
        },
        CHEM_STRUCTURE_WEIGHTS,
    )
    return {
        "detection": detection,
        "iou": iou,
        "label_precision": label_precision,
        "label_recall": label_recall,
        "label_f1": label_f1,
        "extraction": extraction,
        "structured_extraction": structured_extraction,
        "chem_structure_score": score,
    }
