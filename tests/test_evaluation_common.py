import pytest

from pdf_benchmark.evaluation.common import (
    error_rate,
    error_rate_score,
    levenshtein_distance,
    normalized_edit_similarity,
    precision_recall_f1_from_counts,
    reading_order_score,
)


def test_levenshtein_known_example():
    assert levenshtein_distance("kitten", "sitting") == 3


def test_cer_formula_substitution():
    # S=1, D=0, I=0, N=3
    cer = error_rate("abc", "axc")
    assert cer == pytest.approx(1 / 3)
    assert error_rate_score(cer) == pytest.approx(2 / 3)


def test_cer_formula_deletion():
    # D=1 over three reference characters
    assert error_rate("abc", "ac") == pytest.approx(1 / 3)


def test_cer_formula_insertion_can_exceed_one():
    # Reference length N=1, three inserted characters.
    assert error_rate("a", "aaaa") == pytest.approx(3.0)
    assert error_rate_score(3.0) == 0.0


def test_empty_reference_error_rate_convention():
    assert error_rate("", "") == 0.0
    assert error_rate("", "x") == 1.0


def test_normalized_edit_similarity():
    assert normalized_edit_similarity("abc", "abc") == 1.0
    assert normalized_edit_similarity("abc", "xyz") == 0.0


def test_precision_recall_f1_exact_formula():
    # TP=2, FP=1, FN=2:
    # P=2/3, R=2/4=1/2, F1=2PR/(P+R)=4/7
    p, r, f1 = precision_recall_f1_from_counts(2, 1, 2)
    assert p == pytest.approx(2 / 3)
    assert r == pytest.approx(1 / 2)
    assert f1 == pytest.approx(4 / 7)


def test_precision_recall_f1_empty_conventions():
    assert precision_recall_f1_from_counts(0, 0, 0) == (1.0, 1.0, 1.0)

    p, r, f1 = precision_recall_f1_from_counts(0, 0, 2)
    assert (p, r, f1) == (1.0, 0.0, 0.0)

    p, r, f1 = precision_recall_f1_from_counts(0, 2, 0)
    assert (p, r, f1) == (0.0, 1.0, 0.0)


def test_reading_order_ideal_partial_wrong():
    assert reading_order_score(["a", "b", "c"], ["a", "b", "c"])[2] == 1.0

    coverage, pair, score = reading_order_score(
        ["a", "b", "c"],
        ["a", "c"],
    )
    assert coverage == pytest.approx(2 / 3)
    assert pair == 1.0
    assert score == pytest.approx(2 / 3)

    _, pair, score = reading_order_score(
        ["a", "b", "c"],
        ["c", "b", "a"],
    )
    assert pair == 0.0
    assert score == 0.0


def test_reading_order_rejects_duplicates():
    with pytest.raises(ValueError):
        reading_order_score(["a", "a"], ["a"])
