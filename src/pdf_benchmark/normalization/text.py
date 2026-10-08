from __future__ import annotations

import re
from collections.abc import Collection

from .unicode import normalize_unicode

_HORIZONTAL_WS_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
_SINGLE_LINEBREAK_RE = re.compile(r"(?<!\n)\n(?!\n)")

# A visible hyphen at line end is ambiguous: it can be typographic wrapping
# ("электро-\nпроводность") OR a true lexical hyphen ("физико-\nхимический").
# Therefore the default normalizer never deletes a visible hyphen blindly.
_VISIBLE_WRAP_RE = re.compile(
    r"(?P<left>[A-Za-zА-Яа-яЁё]{2,})-\n(?P<right>[A-Za-zА-Яа-яЁё]{2,})"
)
_SOFT_HYPHEN_WRAP_RE = re.compile(
    r"(?P<left>[A-Za-zА-Яа-яЁё]{2,})\u00ad?\n(?P<right>[A-Za-zА-Яа-яЁё]{2,})"
)

_DOUBLE_QUOTES = str.maketrans({
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u00ab": '"',
    "\u00bb": '"',
})
_SINGLE_QUOTES = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
})


def normalize_quotes(text: str) -> str:
    return text.translate(_DOUBLE_QUOTES).translate(_SINGLE_QUOTES)


def _dehyphenate_with_lexicon(text: str, lexicon: Collection[str] | None) -> str:
    """Delete a visible line-end hyphen only when explicitly validated.

    The lexicon contains the *joined* canonical form, e.g.:
        {"электропроводность", "многоуровневый"}

    Without a lexicon the visible hyphen is preserved, preventing the
    normalization layer from hiding a real OCR punctuation error.
    """
    if not lexicon:
        return text

    canonical = {w.casefold() for w in lexicon}

    def repl(match: re.Match[str]) -> str:
        joined = match.group("left") + match.group("right")
        return joined if joined.casefold() in canonical else match.group(0)

    return _VISIBLE_WRAP_RE.sub(repl, text)


def normalize_text(
    value: str | None,
    *,
    preserve_paragraphs: bool = True,
    normalize_quote_glyphs: bool = True,
    visible_hyphen_join_lexicon: Collection[str] | None = None,
) -> str:
    """Normalize ordinary text without correcting recognition errors.

    Rules:
    1. Unicode NFC.
    2. NBSP/narrow spaces -> ordinary spaces.
    3. CRLF/CR -> LF.
    4. Soft hyphen U+00AD is removed when it is a formatting artifact.
    5. A visible '-' at a line break is deleted ONLY for words explicitly
       supplied in visible_hyphen_join_lexicon.
    6. Other line wraps are converted to spaces; paragraph breaks can remain.
    7. Horizontal whitespace is collapsed.
    8. Quote glyph variants are canonicalized, but letter case, punctuation
       content, digits and spelling are never corrected.
    """
    text = normalize_unicode(value)

    # Soft hyphen itself is a discretionary formatting marker.
    text = text.replace("\u00ad\n", "")
    text = text.replace("\u00ad", "")

    text = _dehyphenate_with_lexicon(text, visible_hyphen_join_lexicon)

    # If a visible hyphen is retained, remove only the physical line break.
    # This preserves the hyphen itself:
    #   "физико-\nхимический" -> "физико-химический"
    # and does NOT guess whether it should be deleted.
    text = re.sub(
        r"-(?:[ \t]*)\n(?=[A-Za-zА-Яа-яЁё])",
        "-",
        text,
    )

    if normalize_quote_glyphs:
        text = normalize_quotes(text)

    # First normalize whitespace inside each physical line.
    lines = [_HORIZONTAL_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)

    if preserve_paragraphs:
        # Canonical paragraph separator is exactly two newlines.
        text = _BLANK_LINES_RE.sub("\n\n", text)
        text = _SINGLE_LINEBREAK_RE.sub(" ", text)
    else:
        text = re.sub(r"\s+", " ", text)

    # A line-wrap replacement can introduce doubled spaces.
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def visible_hyphen_candidates(value: str | None) -> list[tuple[str, str, str]]:
    """Return ambiguous visible line-end hyphenation candidates for review.

    Each tuple is (left, right, joined). The function does NOT modify text.
    """
    text = normalize_unicode(value)
    return [
        (m.group("left"), m.group("right"), m.group("left") + m.group("right"))
        for m in _VISIBLE_WRAP_RE.finditer(text)
    ]
