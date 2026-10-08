from __future__ import annotations

from collections.abc import Collection

from .text import normalize_text


def normalize_caption(
    value: str | None,
    *,
    visible_hyphen_join_lexicon: Collection[str] | None = None,
) -> str:
    """Normalize caption text conservatively.

    We intentionally DO NOT:
    - remove "Рис.", "Fig.", "Таблица", numbering or punctuation;
    - lowercase the caption;
    - rewrite the caption wording.

    Those are benchmark content and errors in them must remain measurable.
    """
    return normalize_text(
        value,
        preserve_paragraphs=False,
        normalize_quote_glyphs=True,
        visible_hyphen_join_lexicon=visible_hyphen_join_lexicon,
    )
