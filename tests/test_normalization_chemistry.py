from pdf_benchmark.normalization.chemistry import normalize_chemical_formula


def test_chemical_subscripts():
    assert normalize_chemical_formula("Fe₃O₄") == "Fe3O4"


def test_chemical_hydrate_and_whitespace():
    assert normalize_chemical_formula("FeSO₄ · 6H₂O") == "FeSO4·6H2O"


def test_chemical_charge():
    assert normalize_chemical_formula("SO₄²⁻") == "SO4^2-"
    assert normalize_chemical_formula("NH₄⁺") == "NH4^+"


def test_chemical_latex_style_index_and_charge():
    assert normalize_chemical_formula(r"SO_{4}^{2-}") == "SO4^2-"


def test_chemical_does_not_fix_element_case_or_atoms():
    assert normalize_chemical_formula("Co") != normalize_chemical_formula("CO")
    assert normalize_chemical_formula("Fe2O3") != normalize_chemical_formula("Fe3O4")


def test_chemical_does_not_reorder_formula():
    assert normalize_chemical_formula("H2O") != normalize_chemical_formula("OH2")
