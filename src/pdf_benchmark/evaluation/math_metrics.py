from __future__ import annotations

from pdf_benchmark.normalization.latex import normalize_latex

from .common import (
    latex_tokens,
    multiset_precision_recall_f1,
    normalized_edit_similarity,
    weighted_mean_available,
)


MATH_WEIGHTS = {
    "raw_exact_match": 0.05,
    "normalized_exact_match": 0.35,
    "token_f1": 0.40,
    "edit_similarity": 0.20,
}


def calculate_math_metrics(
    reference_latex: str,
    prediction_latex: str,
    *,
    reference_normalized_latex: str | None = None,
    prediction_normalized_latex: str | None = None,
) -> dict[str, float]:
    ref_raw = reference_latex or ""
    pred_raw = prediction_latex or ""
    ref = (
        reference_normalized_latex
        if reference_normalized_latex is not None
        else normalize_latex(ref_raw)
    )
    pred = (
        prediction_normalized_latex
        if prediction_normalized_latex is not None
        else normalize_latex(pred_raw)
    )

    raw_em = 1.0 if ref_raw == pred_raw else 0.0
    norm_em = 1.0 if ref == pred else 0.0

    token_precision, token_recall, token_f1 = multiset_precision_recall_f1(
        latex_tokens(ref),
        latex_tokens(pred),
    )
    edit_sim = normalized_edit_similarity(ref, pred)

    math_score = weighted_mean_available(
        {
            "raw_exact_match": raw_em,
            "normalized_exact_match": norm_em,
            "token_f1": token_f1,
            "edit_similarity": edit_sim,
        },
        MATH_WEIGHTS,
    )

    return {
        "raw_exact_match": raw_em,
        "normalized_exact_match": norm_em,
        "token_precision": token_precision,
        "token_recall": token_recall,
        "token_f1": token_f1,
        "edit_similarity": edit_sim,
        "math_score": math_score,
    }
