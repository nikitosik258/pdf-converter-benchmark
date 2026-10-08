from __future__ import annotations

import re

from .unicode import normalize_unicode

_SUBSCRIPT_MAP = str.maketrans({
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
})

_SUPERSCRIPT_MAP = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-",
}

_SUPER_RUN_RE = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+")
_LATEX_SUBSCRIPT_RE = re.compile(r"_\{?([0-9]+)\}?")
_LATEX_CHARGE_RE = re.compile(r"\^\{?([0-9]*[+-])\}?")
_MIDDLE_DOTS = {
    "\u22c5": "\u00b7",  # dot operator
    "\u2022": "\u00b7",  # bullet, accepted only inside formula normalization
}


def _normalize_superscript_run(match: re.Match[str]) -> str:
    run = "".join(_SUPERSCRIPT_MAP[ch] for ch in match.group(0))
    return "^" + run


def normalize_chemical_formula(value: str | None) -> str:
    """Normalize a *linear chemical formula* without chemical correction.

    Examples:
        Fe₃O₄ -> Fe3O4
        SO₄²⁻ -> SO4^2-
        NH₄⁺  -> NH4^+
        FeSO₄ · 6H₂O -> FeSO4·6H2O

    Preserved as real content differences:
    - element letter case: Co != CO;
    - element order;
    - coefficients;
    - parentheses/brackets;
    - charge sign/value;
    - dot vs plus;
    - missing/extra atoms.

    We do not balance, parse, reorder or chemically "fix" formulas.
    """
    text = normalize_unicode(value)
    text = text.translate(_SUBSCRIPT_MAP)

    for src, dst in _MIDDLE_DOTS.items():
        text = text.replace(src, dst)

    text = _SUPER_RUN_RE.sub(_normalize_superscript_run, text)

    # Accept common LaTeX-like indexes/charges from formula-aware OCR.
    text = _LATEX_SUBSCRIPT_RE.sub(r"\1", text)
    text = _LATEX_CHARGE_RE.sub(r"^\1", text)

    # Whitespace is never semantically required inside a linear formula.
    text = re.sub(r"\s+", "", text)

    # Canonicalize redundant braces around a charge produced by OCR.
    text = re.sub(r"\^\{([0-9]*[+-])\}", r"^\1", text)
    return text.strip()
