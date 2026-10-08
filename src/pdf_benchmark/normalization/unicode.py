from __future__ import annotations

import re
import unicodedata

# Characters that carry formatting/transport meaning rather than benchmark content.
_ZERO_WIDTH_REMOVE = {
    "\ufeff",  # BOM / zero-width no-break space
    "\u200b",  # zero-width space
    "\u2060",  # word joiner
}

_SPACE_EQUIVALENTS = {
    "\u00a0": " ",  # no-break space
    "\u202f": " ",  # narrow no-break space
    "\u2007": " ",  # figure space
    "\u2009": " ",  # thin space
}

# Preserve semantic roles: en dash and mathematical minus are NOT collapsed.
_PUNCT_EQUIVALENTS = {
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2015": "\u2014",  # horizontal bar -> em dash
}

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_unicode(
    value: str | None,
    *,
    form: str = "NFC",
    normalize_spaces: bool = True,
    normalize_safe_punctuation: bool = True,
    remove_zero_width: bool = True,
    remove_control_chars: bool = True,
) -> str:
    """Conservative Unicode canonicalization.

    Important benchmark rule:
    - NFC is used, NOT NFKC.
    - superscripts/subscripts are preserved here;
    - Greek letters are preserved here;
    - en dash, em dash, minus and ASCII hyphen remain semantically distinct,
      except U+2015 horizontal bar is canonicalized to em dash and Unicode
      hyphen variants U+2010/U+2011 to ASCII hyphen.
    """
    if value is None:
        return ""

    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize(form, text)

    if remove_zero_width:
        for char in _ZERO_WIDTH_REMOVE:
            text = text.replace(char, "")

    if normalize_spaces:
        text = "".join(_SPACE_EQUIVALENTS.get(ch, ch) for ch in text)

    if normalize_safe_punctuation:
        text = "".join(_PUNCT_EQUIVALENTS.get(ch, ch) for ch in text)

    if remove_control_chars:
        text = _CONTROL_RE.sub("", text)

    return text
