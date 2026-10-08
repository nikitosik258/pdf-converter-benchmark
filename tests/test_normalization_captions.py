from pdf_benchmark.normalization.captions import normalize_caption


def test_caption_preserves_label_and_number():
    assert normalize_caption("  Рис.   3.2.\nПередняя панель  ") == "Рис. 3.2. Передняя панель"


def test_caption_does_not_remove_fig_prefix():
    assert normalize_caption("Fig. 7. Architecture") == "Fig. 7. Architecture"
