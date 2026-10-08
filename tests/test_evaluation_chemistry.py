import pytest

from pdf_benchmark.evaluation.chemistry_metrics import (
    calculate_linear_chemistry_metrics,
    calculate_structure_chemistry_metrics,
    parse_chemical_composition,
)
from pdf_benchmark.models import BBox


def test_formula_parser_nested_and_hydrate():
    comp = parse_chemical_composition("FeSO4·(NH4)2SO4·6H2O")
    assert comp == {
        "Fe": 1,
        "S": 2,
        "O": 14,
        "N": 2,
        "H": 20,
    }


def test_linear_chemistry_ideal_unicode_vs_ascii():
    m = calculate_linear_chemistry_metrics("Fe₃O₄", "Fe3O4")
    assert m["normalized_exact_match"] == 1.0
    assert m["composition_similarity"] == 1.0
    assert m["chem_linear_score"] == 1.0


def test_linear_chemistry_partial_error():
    m = calculate_linear_chemistry_metrics("Fe2O3", "Fe3O4")
    assert 0.0 < m["composition_similarity"] < 1.0
    assert 0.0 < m["chem_linear_score"] < 1.0


def test_linear_chemistry_wrong_elements():
    m = calculate_linear_chemistry_metrics("H2", "O2")
    assert m["composition_similarity"] == 0.0


def test_structure_chemistry_ideal():
    box = BBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5)
    m = calculate_structure_chemistry_metrics(
        reference_bbox=box,
        prediction_bbox=box,
        reference_labels=["HO", "SO3Na"],
        prediction_labels=["HO", "SO3Na"],
        prediction_exists=True,
        extraction_success=True,
    )
    assert m["chem_structure_score"] == 1.0
    assert m["structured_extraction"] == 1.0


def test_structure_chemistry_missing():
    box = BBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5)
    m = calculate_structure_chemistry_metrics(
        reference_bbox=box,
        prediction_bbox=None,
        reference_labels=["HO"],
        prediction_labels=[],
        prediction_exists=False,
        extraction_success=False,
    )
    assert m["detection"] == 0.0
    assert m["structured_extraction"] is None
    assert m["chem_structure_score"] == 0.0
