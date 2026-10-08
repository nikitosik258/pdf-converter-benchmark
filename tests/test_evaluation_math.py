from pdf_benchmark.evaluation.math_metrics import calculate_math_metrics


def test_math_ideal():
    m = calculate_math_metrics(r"\frac{x}{y}", r"\frac{x}{y}")
    assert m["raw_exact_match"] == 1.0
    assert m["normalized_exact_match"] == 1.0
    assert m["token_f1"] == 1.0
    assert m["math_score"] == 1.0


def test_math_equivalent_formatting_costs_only_raw_exact_weight():
    m = calculate_math_metrics(r"\dfrac{x}{y}", r"\frac{x}{y}")
    assert m["raw_exact_match"] == 0.0
    assert m["normalized_exact_match"] == 1.0
    assert m["token_f1"] == 1.0
    assert m["edit_similarity"] == 1.0
    assert m["math_score"] == 0.95


def test_math_explicit_normalized_payload_does_not_replace_raw_exact_input():
    m = calculate_math_metrics(
        r"\dfrac{x}{y}",
        r"\frac{x}{y}",
        reference_normalized_latex=r"\frac{x}{y}",
        prediction_normalized_latex=r"\frac{x}{y}",
    )
    assert m["raw_exact_match"] == 0.0
    assert m["normalized_exact_match"] == 1.0
    assert m["math_score"] == 0.95


def test_math_real_error_is_preserved():
    m = calculate_math_metrics("x^2", "x_2")
    assert m["normalized_exact_match"] == 0.0
    assert m["math_score"] < 1.0


def test_math_completely_wrong():
    m = calculate_math_metrics("x", "y")
    assert m["normalized_exact_match"] == 0.0
    assert m["token_f1"] == 0.0
    assert m["edit_similarity"] == 0.0
    assert m["math_score"] == 0.0
