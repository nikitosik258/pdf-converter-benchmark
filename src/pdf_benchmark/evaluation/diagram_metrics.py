from __future__ import annotations

from collections import Counter

from pdf_benchmark.models import BBox
from pdf_benchmark.normalization.text import normalize_text

from .common import bbox_iou, multiset_precision_recall_f1, weighted_mean_available
from .image_metrics import caption_metrics


DIAGRAM_WEIGHTS = {
    "detection": 0.20,
    "iou": 0.15,
    "diagram_text_f1": 0.25,
    "caption": 0.15,
    "key_elements": 0.25,
}


def _text_tokens(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        out.extend(
            normalize_text(value, preserve_paragraphs=False).split()
        )
    return out


def _element_recall(
    reference: list[str],
    prediction: list[str],
) -> float | None:
    if not reference:
        return None

    ref = Counter(
        normalize_text(v, preserve_paragraphs=False) for v in reference
    )
    pred = Counter(
        normalize_text(v, preserve_paragraphs=False) for v in prediction
    )
    tp = sum((ref & pred).values())
    return tp / sum(ref.values())


def _subfigure_score(reference_count: int, prediction_count: int) -> float | None:
    if reference_count == 0:
        return None
    denom = max(reference_count, prediction_count, 1)
    return max(0.0, 1.0 - abs(reference_count - prediction_count) / denom)


def calculate_diagram_metrics(
    *,
    reference_bbox: BBox | None,
    prediction_bbox: BBox | None,
    reference_caption: str | None,
    prediction_caption: str | None,
    reference_text_elements: list[str],
    prediction_text_elements: list[str],
    reference_key_elements: list[str],
    prediction_key_elements: list[str],
    reference_subfigure_count: int,
    prediction_subfigure_count: int,
    prediction_exists: bool,
    detection_score_override: float | None = None,
    detection_metric_name: str = "detection_recall",
) -> dict[str, float | None]:
    detection_score = (
        detection_score_override
        if detection_score_override is not None
        else (1.0 if prediction_exists else 0.0)
    )
    iou = bbox_iou(reference_bbox, prediction_bbox) if prediction_exists else 0.0

    text_p, text_r, text_f1 = multiset_precision_recall_f1(
        _text_tokens(reference_text_elements),
        _text_tokens(prediction_text_elements if prediction_exists else []),
    )

    cap = caption_metrics(
        reference_caption,
        prediction_caption if prediction_exists else "",
    )

    element_recall = _element_recall(
        reference_key_elements,
        prediction_key_elements if prediction_exists else [],
    )
    subfigure = _subfigure_score(
        reference_subfigure_count,
        prediction_subfigure_count if prediction_exists else 0,
    )

    components = [v for v in (element_recall, subfigure) if v is not None]
    key_score = sum(components) / len(components) if components else None

    score = weighted_mean_available(
        {
            "detection": detection_score,
            "iou": iou,
            "diagram_text_f1": text_f1,
            "caption": cap["caption"],
            "key_elements": key_score,
        },
        DIAGRAM_WEIGHTS,
    )

    return {
        detection_metric_name: detection_score,
        "detection_score": detection_score,
        "iou": iou,
        "diagram_text_precision": text_p,
        "diagram_text_recall": text_r,
        "diagram_text_f1": text_f1,
        **cap,
        "element_recall": element_recall,
        "subfigure_score": subfigure,
        "key_elements": key_score,
        "diagram_score": score,
    }
