import pytest

from pdf_benchmark.evaluation.text_metrics import calculate_text_metrics


def test_text_ideal():
    m = calculate_text_metrics(
        "Это тест.",
        "Это тест.",
        reference_order=["a", "b"],
        prediction_order=["a", "b"],
    )
    assert m["cer"] == 0.0
    assert m["wer"] == 0.0
    assert m["text_score"] == 1.0


def test_wer_exact_formula():
    # Reference words: [one, two, three]
    # Prediction:      [one, too, three]
    # one substitution / 3 reference words
    m = calculate_text_metrics("one two three", "one too three")
    assert m["wer"] == pytest.approx(1 / 3)


def test_text_partial():
    m = calculate_text_metrics("модель данных", "модль данных")
    assert 0.0 < m["text_score"] < 1.0


def test_text_completely_wrong():
    m = calculate_text_metrics("abc", "xyz")
    assert m["cer"] == 1.0
    assert m["wer"] == 1.0
    assert m["edit_similarity"] == 0.0
    assert m["text_score"] == 0.0
