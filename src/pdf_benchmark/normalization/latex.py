from __future__ import annotations

import re

from .unicode import normalize_unicode

# Unicode Greek is converted to the corresponding LaTeX token.
# Variant glyphs stay variants where Unicode distinguishes them.
_GREEK = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "ϵ": r"\varepsilon", "ζ": r"\zeta", "η": r"\eta",
    "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi",
    "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho",
    "ϱ": r"\varrho", "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau",
    "υ": r"\upsilon", "φ": r"\phi", "ϕ": r"\varphi", "χ": r"\chi",
    "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon",
    "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}

_SPACING_COMMAND_RE = re.compile(
    r"(?:\\,|\\;|\\:|\\!|\\quad\b|\\qquad\b|\\enspace\b|\\thinspace\b)"
)
_CONTROL_SPACE_RE = re.compile(r"(?<!\\)\\(?!\\)[ \t\r\n]+")
_SCRIPT_TEXT_COMMANDS = (
    r"text|textrm|textsf|texttt|operatorname|mathrm|mathbf|mathit|mathsf|mathtt"
)
_SIMPLE_SCRIPT_RE = re.compile(
    rf"(?P<op>[_^])\s*(?P<atom>"
    rf"\\(?:{_SCRIPT_TEXT_COMMANDS})\s*\{{[^{{}}]*\}}|"
    r"\\[A-Za-z]+|[A-Za-z0-9])(?=$|[^A-Za-z])"
)
_OUTER_DELIMITERS = (
    (r"\(", r"\)"),
    (r"\[", r"\]"),
)

# Commands that differ only in display sizing for our recognition metric.
_EQUIVALENT_COMMANDS = {
    r"\dfrac": r"\frac",
    r"\tfrac": r"\frac",
}


def _strip_math_delimiters(text: str) -> str:
    s = text.strip()
    if len(s) >= 2 and s.startswith("$") and s.endswith("$"):
        if not (s.startswith("$$") and not s.endswith("$$")):
            s = s[1:-1].strip()
            if s.startswith("$") and s.endswith("$"):
                s = s[1:-1].strip()

    for left, right in _OUTER_DELIMITERS:
        if s.startswith(left) and s.endswith(right):
            s = s[len(left):-len(right)].strip()
    return s


_FRAC_ATOM = r"(\\[A-Za-z]+|[A-Za-z0-9])"
_FRAC_SIMPLE_RE = re.compile(r"\\frac\s*" + _FRAC_ATOM + r"\s*" + _FRAC_ATOM)
_FRAC_LEFT_BRACED_RE = re.compile(r"\\frac\s*(\{[^{}]*\})\s*" + _FRAC_ATOM)
_FRAC_RIGHT_BRACED_RE = re.compile(r"\\frac\s*" + _FRAC_ATOM + r"\s*(\{[^{}]*\})")


def _canonicalize_simple_frac(text: str) -> str:
    """Canonicalize valid simple unbraced \\frac arguments.

    Safe examples:
        \\frac12       -> \\frac{1}{2}
        \\frac\\alpha2 -> \\frac{\\alpha}{2}
        \\frac{x}2     -> \\frac{x}{2}

    Nested brace parsing is intentionally not attempted here.
    """
    def both(match: re.Match[str]) -> str:
        return rf"\frac{{{match.group(1)}}}{{{match.group(2)}}}"

    def left(match: re.Match[str]) -> str:
        return rf"\frac{match.group(1)}{{{match.group(2)}}}"

    def right(match: re.Match[str]) -> str:
        return rf"\frac{{{match.group(1)}}}{match.group(2)}"

    text = _FRAC_LEFT_BRACED_RE.sub(left, text)
    text = _FRAC_RIGHT_BRACED_RE.sub(right, text)
    text = _FRAC_SIMPLE_RE.sub(both, text)
    return text


def _canonicalize_scripts(text: str) -> str:
    # Repeat because a formula can contain many occurrences.
    previous = None
    while previous != text:
        previous = text

        def repl(match: re.Match[str]) -> str:
            op = match.group("op")
            atom = match.group("atom")
            return f"{op}{{{atom}}}"

        text = _SIMPLE_SCRIPT_RE.sub(repl, text)
    return text


def _remove_insignificant_spaces(text: str) -> str:
    r"""Remove spaces outside text-like command arguments.

    LaTeX spaces inside \text{...}, \operatorname{...}, \mathrm{...},
    \mathbf{...}, etc. may carry visual/content meaning, so they are protected.
    """
    protected: list[str] = []

    command_re = re.compile(
        r"\\(?P<cmd>text|textrm|textsf|texttt|operatorname|mathrm|mathbf|mathit|mathsf|mathtt)\s*\{"
    )
    i = 0
    out = []
    while i < len(text):
        m = command_re.match(text, i)
        if not m:
            out.append(text[i])
            i += 1
            continue

        start = m.start()
        brace_start = m.end() - 1
        depth = 0
        j = brace_start
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1

        if depth != 0:
            out.append(text[i])
            i += 1
            continue

        chunk = text[start:j + 1]
        token = f"@@PROTECTED_{len(protected)}@@"
        protected.append(chunk)
        out.append(token)
        i = j + 1

    compact = "".join(out)
    # A whitespace run after a control word is a lexical delimiter when the
    # following token starts with a letter. Deleting it changes, for example,
    # ``\alpha x`` into the different command ``\alphax``. Canonicalize that
    # delimiter to one ordinary space before removing other math whitespace.
    command_boundaries: list[str] = []

    def protect_command_boundary(match: re.Match[str]) -> str:
        token = f"@@COMMAND_BOUNDARY_{len(command_boundaries)}@@"
        command_boundaries.append(token)
        return match.group("command") + token

    compact = re.sub(
        r"(?P<command>\\[A-Za-z]+)\s+(?=[A-Za-z])",
        protect_command_boundary,
        compact,
    )
    compact = re.sub(r"\s+", "", compact)

    for token in command_boundaries:
        compact = compact.replace(token, " ")

    for idx, chunk in enumerate(protected):
        compact = compact.replace(f"@@PROTECTED_{idx}@@", chunk)

    return compact


def _replace_unicode_greek(text: str) -> str:
    """Convert glyphs without merging a new control word with Latin letters."""
    out: list[str] = []
    for index, char in enumerate(text):
        command = _GREEK.get(char)
        if command is None:
            out.append(char)
            continue
        out.append(command)
        if index + 1 < len(text) and re.match(r"[A-Za-z]", text[index + 1]):
            out.append(" ")
    return "".join(out)


def normalize_latex(value: str | None) -> str:
    r"""Canonicalize superficial LaTeX representation differences.

    Intentionally NOT normalized:
    - '+' vs '-';
    - subscript vs superscript;
    - different numbers/variables;
    - \cdot vs \times;
    - \phi vs \varphi;
    - algebraically equivalent but visually different expressions;
    - bracket type: (), [], {} remain different.

    Therefore this function removes formatting variance but not mathematical
    recognition errors.
    """
    text = normalize_unicode(value)
    text = _strip_math_delimiters(text)

    text = _replace_unicode_greek(text)

    for source, target in _EQUIVALENT_COMMANDS.items():
        text = text.replace(source, target)

    # Size modifiers do not change the represented brackets.
    text = re.sub(r"\\left\b", "", text)
    text = re.sub(r"\\right\b", "", text)
    text = re.sub(r"\\bigl\b|\\bigr\b|\\Bigl\b|\\Bigr\b|\\biggl\b|\\biggr\b|\\Biggl\b|\\Biggr\b", "", text)

    # A single backslash followed by whitespace is TeX's control-space. Remove
    # it before general whitespace compaction; otherwise ``\ ,`` becomes the
    # different token ``\,`` and ``\ j`` becomes the control word ``\j`` on
    # the first pass, making a second normalization change the result again.
    # Doubled backslashes (row separators) are deliberately excluded.
    text = _CONTROL_SPACE_RE.sub("", text)

    # Pure visual spacing commands.
    text = _SPACING_COMMAND_RE.sub("", text)

    text = _canonicalize_simple_frac(text)
    text = _canonicalize_scripts(text)
    text = _remove_insignificant_spaces(text)
    return text.strip()
