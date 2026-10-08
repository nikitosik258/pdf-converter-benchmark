"""Whitespace normalization may not change LaTeX control-word tokens."""
import pytest

from pdf_benchmark.normalization.latex import normalize_latex


@pytest.mark.parametrize("source,expected", [
    (r"\alpha x", r"\alpha x"),
    (r"\alpha   x", r"\alpha x"),
    (r"\pi n x", r"\pi nx"),
    (r"\partial E", r"\partial E"),
    (r"\sin theta", r"\sin theta"),
    (r"\cdot R", r"\cdot R"),
    (r"\unknown command", r"\unknown command"),
])
def test_space_delimiting_control_word_from_letter_is_canonicalized(source, expected):
    assert normalize_latex(source) == expected


@pytest.mark.parametrize("source,expected", [
    (r"\alpha + x", r"\alpha+x"),
    (r"\alpha {x}", r"\alpha{x}"),
    (r"\alpha \beta", r"\alpha\beta"),
    (r"\sin 2x", r"\sin2x"),
    (r"x y", "xy"),
])
def test_spaces_without_control_word_boundary_remain_insignificant(source, expected):
    assert normalize_latex(source) == expected


@pytest.mark.parametrize("source,expected", [
    ("αx", r"\alpha x"),
    ("πn", r"\pi n"),
    ("α+x", r"\alpha+x"),
    ("xαy", r"x\alpha y"),
])
def test_unicode_greek_conversion_creates_required_command_boundary(source, expected):
    assert normalize_latex(source) == expected


def test_distinct_control_word_and_longer_command_do_not_collapse_together():
    assert normalize_latex(r"\alpha x") != normalize_latex(r"\alphax")
    assert normalize_latex(r"\partial E") != normalize_latex(r"\partialE")


def test_protected_text_like_arguments_and_nested_braces_still_preserve_spaces():
    assert normalize_latex(r"\operatorname{arg max} x") == r"\operatorname{arg max}x"
    assert normalize_latex(r"\text{force per {unit area}} + x") == r"\text{force per {unit area}}+x"
