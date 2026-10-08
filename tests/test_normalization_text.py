from pdf_benchmark.normalization.text import (
    normalize_text,
    visible_hyphen_candidates,
)


def test_text_whitespace_and_line_wraps():
    raw = "Первая   строка,\nкоторая\tпродолжается.\n\nНовый   абзац."
    assert normalize_text(raw) == "Первая строка, которая продолжается.\n\nНовый абзац."


def test_quotes_are_format_normalized():
    assert normalize_text("«текст» “text”") == '"текст" "text"'


def test_soft_hyphen_is_formatting():
    assert normalize_text("электро\u00ad\nпроводность") == "электропроводность"


def test_visible_line_hyphen_not_deleted_without_validation():
    assert normalize_text("физико-\nхимический") == "физико-химический"
    assert normalize_text("электро-\nпроводность") == "электро-проводность"


def test_visible_line_hyphen_deleted_only_with_explicit_joined_word():
    assert normalize_text(
        "электро-\nпроводность",
        visible_hyphen_join_lexicon={"электропроводность"},
    ) == "электропроводность"

    # Lexicon must not make a different lexical hyphen disappear.
    assert normalize_text(
        "физико-\nхимический",
        visible_hyphen_join_lexicon={"электропроводность"},
    ) == "физико-химический"


def test_hyphen_candidates_are_reported_not_silently_fixed():
    assert visible_hyphen_candidates("электро-\nпроводность") == [
        ("электро", "проводность", "электропроводность")
    ]


def test_text_does_not_correct_real_ocr_errors():
    assert normalize_text("модль") == "модль"
    assert normalize_text("x₃") == "x₃"
