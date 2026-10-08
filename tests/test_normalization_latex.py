from pdf_benchmark.normalization.latex import normalize_latex


def test_latex_display_frac_variants():
    assert normalize_latex(r"\dfrac{x}{y}") == normalize_latex(r"\frac{x}{y}")
    assert normalize_latex(r"\tfrac{x}{y}") == normalize_latex(r"\frac{x}{y}")


def test_latex_math_delimiters_and_spacing():
    assert normalize_latex(r"$  \frac{ x }{ y }  $") == r"\frac{x}{y}"
    assert normalize_latex(r"\left( x + y \right)") == "(x+y)"


def test_latex_scripts_canonicalized():
    assert normalize_latex("x^2 + y_1") == r"x^{2}+y_{1}"


def test_latex_unicode_greek():
    assert normalize_latex("α + β_2") == r"\alpha+\beta_{2}"


def test_latex_variant_greek_is_not_collapsed():
    assert normalize_latex("φ") == r"\phi"
    assert normalize_latex("ϕ") == r"\varphi"
    assert normalize_latex("φ") != normalize_latex("ϕ")


def test_latex_preserves_real_math_errors():
    assert normalize_latex("x^2") != normalize_latex("x_2")
    assert normalize_latex("x+1") != normalize_latex("x-1")
    assert normalize_latex(r"a\cdot b") != normalize_latex(r"a\times b")


def test_latex_preserves_text_spaces():
    assert normalize_latex(r"\text{hello world} + x") == r"\text{hello world}+x"


def test_latex_simple_frac_arguments_are_canonicalized():
    assert normalize_latex(r"\frac12") == r"\frac{1}{2}"
    assert normalize_latex(r"\frac{x}2") == r"\frac{x}{2}"
    assert normalize_latex(r"\frac\alpha2") == r"\frac{\alpha}{2}"


def test_latex_control_space_is_removed_before_spacing_commands():
    assert normalize_latex(r"x\ , y") == "x,y"
    assert normalize_latex(r"x\ j") == "xj"
    assert normalize_latex(r"x\\ y") == r"x\\y"


def test_latex_scripts_allow_insignificant_space_before_the_atom():
    assert normalize_latex(r"x_ i") == r"x_{i}"
    assert normalize_latex(r"x_ \alpha") == r"x_{\alpha}"
    assert normalize_latex(r"x_ \mathrm { C H }") == r"x_{\mathrm { C H }}"


def test_latex_normalization_is_idempotent_for_previously_failing_shapes():
    sources = [
        r"C _ { s } = \frac { q _ { s } S } { \Delta \varphi } \ ,",
        r"t _ { e j } = \frac { d } { \mu _ { j } \, E _ { 0 } } \ , \ j = 1 , 2",
        r"\gamma _ { \mathrm { C H } } = \frac { \Delta _ { _ \mathrm { \pi o n } } } { x _ { _ \mathrm { \pi p M } } }",
        r"\Delta _ { x } = \frac { \chi _ { _ \mathrm { { H p",
    ]
    for source in sources:
        once = normalize_latex(source)
        assert normalize_latex(once) == once
