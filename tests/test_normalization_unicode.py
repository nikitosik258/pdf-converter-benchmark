from pdf_benchmark.normalization.unicode import normalize_unicode


def test_unicode_uses_nfc_not_nfkc():
    # NFC composes accents but must not flatten semantic superscripts.
    assert normalize_unicode("e\u0301") == "é"
    assert normalize_unicode("x²") == "x²"


def test_unicode_space_equivalents():
    assert normalize_unicode("a\u00a0b\u202fc\u2007d") == "a b c d"


def test_unicode_zero_width_removed():
    assert normalize_unicode("ab\u200bcd\ufeff") == "abcd"


def test_unicode_semantic_dashes_preserved():
    text = "a-b a–b a—b a−b"
    assert normalize_unicode(text) == text


def test_unicode_safe_hyphen_variants():
    assert normalize_unicode("a‐b a‑b") == "a-b a-b"
