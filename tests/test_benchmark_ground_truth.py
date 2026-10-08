import json
from pathlib import Path

import pytest

from pdf_benchmark.benchmark.ground_truth import (
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)


def test_load_jsonl_and_manifest_validation(tmp_path):
    gt = tmp_path / "gt"
    gt.mkdir()
    rows = [
        {
            "object_id": "TXT_1",
            "document_id": "D1",
            "page": 1,
            "object_type": "text",
            "raw_text": "abc",
        },
        {
            "object_id": "MATH_1",
            "document_id": "D1",
            "page": 2,
            "object_type": "math",
            "latex": "x^2",
        },
    ]
    with (gt / "objects.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    objects = load_ground_truth(gt)
    assert objects[0].reference["text"] == "abc"
    assert objects[1].object_type == "math_formula"

    manifest = [
        {"object_id": "TXT_1", "document_id": "D1", "page": 1, "object_type": "text"},
        {"object_id": "MATH_1", "document_id": "D1", "page": 2, "object_type": "math_formula"},
    ]
    validate_ground_truth_manifest(objects, manifest, selected_documents={"D1"})


def test_manifest_catches_missing_object(tmp_path):
    gt = tmp_path / "gt"
    gt.mkdir()
    (gt / "objects.json").write_text(
        json.dumps([
            {
                "object_id": "A",
                "document_id": "D1",
                "page": 1,
                "object_type": "text",
                "reference": {"text": "a"},
            }
        ]),
        encoding="utf-8",
    )
    objects = load_ground_truth(gt)
    with pytest.raises(ValueError, match="missing"):
        validate_ground_truth_manifest(
            objects,
            [
                {"object_id": "A", "document_id": "D1", "page": 1, "object_type": "text"},
                {"object_id": "B", "document_id": "D1", "page": 1, "object_type": "text"},
            ],
        )


def test_fractional_operator_gt_uses_left_subscript_t_from_pdf():
    project_root = Path(__file__).resolve().parents[1]
    objects = load_ground_truth(project_root / "ground_truth")
    by_id = {obj.object_id: obj for obj in objects}

    assert by_id["MATH_002"].reference["latex"].startswith(r"{}_{t}D_*^{\alpha}")
    assert by_id["MATH_003"].reference["latex"].startswith(r"{}_{t}D^{\alpha}")
    assert by_id["MATH_004"].reference["latex"].startswith(r"{}_{t}D_*^{\alpha}")
    assert all(
        r"{}_{0}^{t}" not in by_id[object_id].reference["latex"]
        for object_id in ("MATH_002", "MATH_003", "MATH_004")
    )

    manifest = load_object_manifest(project_root / "benchmark" / "object_manifest.json")
    validate_ground_truth_manifest(objects, manifest)


def test_math_005_gt_is_single_numbered_equation_six():
    project_root = Path(__file__).resolve().parents[1]
    objects = load_ground_truth(project_root / "ground_truth")
    math_005 = {obj.object_id: obj for obj in objects}["MATH_005"]

    expected_bbox = [0.310924, 0.611639, 0.815126, 0.659145]
    assert list(math_005.bbox.model_dump().values()) == expected_bbox
    assert list(math_005.reference["bbox"].values()) == expected_bbox
    assert math_005.reference["latex"] == (
        r"u(x,t)=\sum_{n=0}^{\infty}C_nE_{\alpha}"
        r"(-\pi^2n^2\lambda^2t^{\alpha})\sin(\pi n x)"
    )


def test_math_006_gt_covers_complete_multiline_equation_57():
    project_root = Path(__file__).resolve().parents[1]
    objects = load_ground_truth(project_root / "ground_truth")
    math_006 = {obj.object_id: obj for obj in objects}["MATH_006"]

    expected_bbox = [0.100847, 0.211391, 0.504236, 0.397843]
    assert list(math_006.bbox.model_dump().values()) == expected_bbox
    assert list(math_006.reference["bbox"].values()) == expected_bbox
    assert math_006.reference["latex"] == (
        r"j(t)=j_e(t)+j_i(t)+j_d(t),\quad "
        r"j_e(t)=\varepsilon\varepsilon_0\left.\frac{\partial E_1}{\partial t}"
        r"\right|_{x=0},\quad "
        r"j_i(t)=e(\mu_1n_{10}+\mu_2n_{20})E_0+"
        r"\left.e\mu_2n_{20}E_1\right|_{x=0},\quad "
        r"j_d(t)=\left.e\mu_2n_{21}\right|_{x=0}E_0,\quad "
        r"\left.E_1\right|_{x=0}=\frac{en_0}{\varepsilon\varepsilon_0d}"
        r"\left[(x_1+x_2)d-\frac{d^2}{2}-\frac{1}{2}(x_1^2+x_2^2)\right],\quad "
        r"\left.n_{21}\right|_{x=0}=\left(k_dN-\mu_2\frac{en_0^2}"
        r"{\varepsilon_0\varepsilon}\right)\frac{x_1}{V_2},\quad t\le t_*;\quad "
        r"\left.n_{21}\right|_{x=0}=k_dN\frac{d}{V_2}-\mu_2"
        r"\frac{en_0^2}{\varepsilon\varepsilon_0}\frac{x_2}{V_2},\quad t>t_*"
    )


def test_chem_001_gt_uses_complete_native_pdf_word_bbox():
    project_root = Path(__file__).resolve().parents[1]
    objects = load_ground_truth(project_root / "ground_truth")
    chem_001 = {obj.object_id: obj for obj in objects}["CHEM_001"]

    expected_bbox = [0.292984, 0.146948, 0.386878, 0.165088]
    assert list(chem_001.bbox.model_dump().values()) == expected_bbox
    assert list(chem_001.reference["bbox"].values()) == expected_bbox
    assert chem_001.reference["formula"] == "FeCl3·6H2O"


def test_chem_002_gt_uses_complete_native_pdf_word_bbox():
    project_root = Path(__file__).resolve().parents[1]
    objects = load_ground_truth(project_root / "ground_truth")
    chem_002 = {obj.object_id: obj for obj in objects}["CHEM_002"]

    expected_bbox = [0.29518, 0.163128, 0.49268, 0.181268]
    assert list(chem_002.bbox.model_dump().values()) == expected_bbox
    assert list(chem_002.reference["bbox"].values()) == expected_bbox
    assert chem_002.reference["formula"] == "FeSO4·(NH4)2SO4·6H2O"
