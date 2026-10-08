from __future__ import annotations

from pdf_benchmark.models import BBox
from pdf_benchmark.normalization.captions import normalize_caption

from .common import (
    bbox_iou,
    multiset_precision_recall_f1,
    normalized_edit_similarity,
    precision_recall_f1_from_counts,
    weighted_mean_available,
)


IMAGE_WEIGHTS = {
    "detection": 0.30,
    "iou": 0.25,
    "extraction": 0.25,
    "caption": 0.20,
}


def detection_metrics(tp: int, fp: int, fn: int) -> dict[str, float]:
    p, r, f1 = precision_recall_f1_from_counts(tp, fp, fn)
    return {
        "detection_precision": p,
        "detection_recall": r,
        "detection_f1": f1,
    }


def caption_metrics(
    reference_caption: str | None,
    prediction_caption: str | None,
) -> dict[str, float | None]:
    # If GT intentionally has no caption, caption metric is not applicable.
    if reference_caption is None:
        return {
            "caption_token_precision": None,
            "caption_token_recall": None,
            "caption_token_f1": None,
            "caption_edit_similarity": None,
            "caption": None,
        }

    ref = normalize_caption(reference_caption)
    pred = normalize_caption(prediction_caption or "")
    p, r, f1 = multiset_precision_recall_f1(ref.split(), pred.split())
    edit = normalized_edit_similarity(ref, pred)
    return {
        "caption_token_precision": p,
        "caption_token_recall": r,
        "caption_token_f1": f1,
        "caption_edit_similarity": edit,
        "caption": (f1 + edit) / 2.0,
    }


def calculate_image_metrics(
    *,
    reference_bbox: BBox | None,
    prediction_bbox: BBox | None,
    reference_caption: str | None,
    prediction_caption: str | None,
    prediction_exists: bool,
    extraction_success: bool,
    detection_score_override: float | None = None,
    detection_metric_name: str = "detection_recall",
) -> dict[str, float | None]:
    detection_score = (
        detection_score_override
        if detection_score_override is not None
        else (1.0 if prediction_exists else 0.0)
    )
    iou = bbox_iou(reference_bbox, prediction_bbox) if prediction_exists else 0.0
    extraction = 1.0 if (prediction_exists and extraction_success) else 0.0
    cap = caption_metrics(
        reference_caption,
        prediction_caption if prediction_exists else "",
    )

    score = weighted_mean_available(
        {
            "detection": detection_score,
            "iou": iou,
            "extraction": extraction,
            "caption": cap["caption"],
        },
        IMAGE_WEIGHTS,
    )

    return {
        detection_metric_name: detection_score,
        "detection_score": detection_score,
        "iou": iou,
        "extraction": extraction,
        **cap,
        "image_score": score,
    }
