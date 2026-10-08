from pdf_benchmark.models import BBox, Formula, Page, StandardizedDocument, TextBlock, ToolMetadata
from pdf_benchmark.standardization.math import (
    delimited_math_candidates,
    enrich_math_formulas,
)


def _block(element_id, text, x0, y0, x1, y1, order):
    return TextBlock(
        element_id=element_id,
        page_number=1,
        bbox=BBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1),
        raw_text=text,
        order_index=order,
    )


def _document(tool, blocks, formulas=None):
    return StandardizedDocument(
        document_id="D-test",
        source_pdf="test.pdf",
        tool=ToolMetadata(tool_name=tool, distribution_name=tool),
        pages=[
            Page(
                page_number=1,
                width=1,
                height=1,
                text_blocks=blocks,
                formulas=formulas or [],
                reading_order=[block.element_id for block in blocks],
            )
        ],
    )


def test_delimited_math_keeps_standalone_display_and_ignores_inline_variable():
    text = "Prose with $x$ inline.\n\\(x^2 + y^2 = z^2\\) (7)\n"
    candidates = delimited_math_candidates(text)
    assert [item.latex for item in candidates] == ["x^2 + y^2 = z^2"]
    assert candidates[0].numbered is True


def test_shared_math_groups_consecutive_numbered_display_lines():
    blocks = [
        _block("f1", "$$j=j_e+j_i+j_d\\tag{57}$$", .20, .20, .80, .22, 1),
        _block("f2", "$$j_e=\\varepsilon\\varepsilon_0 E_t$$", .20, .23, .75, .25, 2),
        _block("f3", "$$j_i=e(\\mu_1n_1+\\mu_2n_2)E_0$$", .20, .26, .78, .28, 3),
        _block("p", "Здесь приведены обозначения величин.", .10, .31, .85, .34, 4),
    ]
    document = enrich_math_formulas(_document("ocr_space", blocks))
    formulas = document.pages[0].formulas
    groups = [
        item for item in formulas
        if item.provenance.get("source") == "numbered_delimited_display_group_v1"
    ]
    assert len(groups) == 1
    assert groups[0].provenance["group_size"] == 3
    assert "j_e=" in groups[0].latex and "j_i=" in groups[0].latex


def test_numbered_layout_fallback_emits_plain_payload_without_latex():
    blocks = [
        _block("eq-a", "u(x,t) =", .20, .20, .36, .22, 1),
        _block("eq-b", "sum C_n sin(pi n x)", .37, .20, .70, .22, 2),
        _block("number", "(6)", .82, .20, .86, .22, 3),
        _block("prose", "Функция u задается приведенным выше равенством.", .12, .26, .85, .29, 4),
    ]
    document = enrich_math_formulas(_document("pdfminer", blocks))
    formulas = document.pages[0].formulas
    assert len(formulas) == 1
    assert formulas[0].latex is None
    assert formulas[0].raw_text == "u(x,t) = sum C_n sin(pi n x)"
    assert formulas[0].provenance["latex_reconstructed"] is False
    assert "prose" not in formulas[0].provenance["source_element_ids"]


def test_parenthetical_prose_reference_does_not_become_formula():
    blocks = [
        _block("prose", "Как показано в выражении", .15, .20, .55, .22, 1),
        _block("number", "(3)", .82, .20, .86, .22, 2),
    ]
    document = enrich_math_formulas(_document("mindee", blocks))
    assert document.pages[0].formulas == []


def test_number_in_other_column_does_not_borrow_distant_formula():
    blocks = [
        _block("left-math", "x = y + 1", .08, .20, .32, .22, 1),
        _block("right-number", "(3)", .78, .20, .82, .22, 2),
    ]
    document = enrich_math_formulas(_document("pdfminer", blocks))
    assert document.pages[0].formulas == []


def test_native_formula_is_preserved_and_blocks_layout_fallback():
    blocks = [
        _block("eq", "x = 1", .20, .20, .40, .22, 1),
        _block("number", "(1)", .82, .20, .86, .22, 2),
    ]
    native = Formula(
        element_id="native",
        page_number=1,
        latex="x=1",
        formula_type="display",
    )
    document = enrich_math_formulas(_document("pdfminer", blocks, [native]))
    assert [item.element_id for item in document.pages[0].formulas] == ["native"]
