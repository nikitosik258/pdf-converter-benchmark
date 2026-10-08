from __future__ import annotations

from pdf_benchmark.normalization.text import normalize_text

from .common import (
    error_rate,
    error_rate_score,
    normalized_edit_similarity,
    reading_order_score,
    weighted_mean_available,
    word_tokens,
)


TEXT_WEIGHTS = {
    "cer_score": 0.40,
    "wer_score": 0.30,
    "edit_similarity": 0.10,
    "reading_order": 0.20,
}


def calculate_text_metrics(
    reference: str,
    prediction: str,
    *,
    reference_order: list[str] | None = None,
    prediction_order: list[str] | None = None,
) -> dict[str, float | None]:
    ref = normalize_text(reference)
    pred = normalize_text(prediction)

    cer = error_rate(ref, pred)
    ref_words = word_tokens(ref)
    pred_words = word_tokens(pred)
    wer = error_rate(ref_words, pred_words)
    edit_sim = normalized_edit_similarity(ref, pred)

    ro = None
    ro_coverage = None
    ro_pair_order = None
    if reference_order is not None:
        coverage, pair_order, ro = reading_order_score(
            reference_order,
            prediction_order or [],
        )
        ro_coverage = coverage
        ro_pair_order = pair_order

    values = {
        "cer_score": error_rate_score(cer),
        "wer_score": error_rate_score(wer),
        "edit_similarity": edit_sim,
        "reading_order": ro,
    }
    text_score = weighted_mean_available(values, TEXT_WEIGHTS)

    return {
        "cer": cer,
        "cer_score": values["cer_score"],
        "wer": wer,
        "wer_score": values["wer_score"],
        "edit_similarity": edit_sim,
        "reading_order_coverage": ro_coverage,
        "reading_order_pair_order": ro_pair_order,
        "reading_order": ro,
        "text_score": text_score,
    }
