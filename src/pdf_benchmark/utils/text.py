from __future__ import annotations

import re
from html import unescape

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None


def collapse_ws(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    if BeautifulSoup is None:
        return collapse_ws(re.sub(r"<[^>]+>", " ", unescape(html)))
    soup = BeautifulSoup(html, "html.parser")
    return collapse_ws(soup.get_text(" ", strip=True))


def strip_math_wrappers(text: str | None) -> str:
    if not text:
        return ""
    value = text.strip()
    for left, right in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)")):
        if value.startswith(left) and value.endswith(right):
            value = value[len(left): -len(right)].strip()
    return value
