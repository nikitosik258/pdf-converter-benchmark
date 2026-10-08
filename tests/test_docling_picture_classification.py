"""Classify saved Docling pictures using docling-core only; no model execution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("docling_core")
from docling_core.types.doc import DoclingDocument
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.items.picture.meta import PictureMeta

from pdf_benchmark.adapters.local import DoclingAdapter
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.utils.io import write_json


def _standardize(tmp_path, meta):
    native = DoclingDocument(name="picture_regression")
    native.add_page(page_no=1, size=Size(width=100, height=200))
    picture = native.add_picture(prov=ProvenanceItem(
        page_no=1, charspan=(0, 0),
        bbox=BoundingBox(l=10, t=20, r=80, b=100, coord_origin=CoordOrigin.TOPLEFT),
    ))
    if meta is not None:
        picture.meta = PictureMeta.model_validate(meta)
    raw = tmp_path / "raw"
    path = write_json(raw / "document.json", native.export_to_dict())
    asset = raw / "picture.png"
    asset.write_bytes(b"cached image bytes")
    write_json(raw / "asset_map.json", {picture.self_ref: str(asset)})
    assets = tmp_path / "assets"
    assets.mkdir()
    doc = DoclingAdapter().standardize(
        RawToolResult(primary_artifact=str(path)), raw, assets,
        document_id="T01", pdf_path=tmp_path / "unused.pdf",
    )
    page = doc.pages[0]
    objects = [*page.images, *page.diagrams]
    assert len(objects) == 1
    obj = objects[0]
    assert page.reading_order == [picture.self_ref]
    assert obj.element_id == picture.self_ref
    assert (obj.bbox.x_min, obj.bbox.y_min, obj.bbox.x_max, obj.bbox.y_max) == (.1, .1, .8, .5)
    assert (tmp_path / obj.asset_path).read_bytes() == asset.read_bytes()
    assert not page.chemical_objects
    return page


@pytest.mark.parametrize("sample_index,expected", [(0, "photograph"), (1, "logo")])
def test_docling_real_alternative_classes_do_not_override_main_prediction(tmp_path, sample_index, expected):
    fixture = Path(__file__).parent / "fixtures/local/docling_picture_classification.json"
    meta = json.loads(fixture.read_text(encoding="utf-8"))[sample_index]["meta"]
    page = _standardize(tmp_path, meta)
    assert len(page.images) == 1
    assert page.images[0].extraction_success
    assert page.images[0].provenance["selected_classification"]["class_name"] == expected


@pytest.mark.parametrize("class_name,diagram_type", [
    ("bar_chart", "chart"), ("box_plot", "chart"), ("line_chart", "chart"),
    ("pie_chart", "chart"), ("scatter_plot", "chart"), ("other_chart", "chart"),
    ("flow_chart", "flowchart"), ("engineering_drawing", "scientific_scheme"),
])
def test_docling_diagram_mapping_uses_selected_native_class(tmp_path, class_name, diagram_type):
    page = _standardize(tmp_path, {"classification": {"predictions": [
        {"class_name": "photograph", "confidence": .1},
        {"class_name": class_name, "confidence": .9},
    ]}})
    assert len(page.diagrams) == 1
    assert page.diagrams[0].diagram_type == diagram_type
    assert page.diagrams[0].provenance["selected_classification"]["class_name"] == class_name


@pytest.mark.parametrize("class_name", [
    "photograph", "logo", "other", "geographical_map", "topographical_map",
    "chemistry_structure", "table", "unknown_chart_feature",
])
def test_docling_non_diagram_main_class_is_an_image_despite_chart_alternative(tmp_path, class_name):
    page = _standardize(tmp_path, {"classification": {"predictions": [
        {"class_name": "flow_chart", "confidence": .1},
        {"class_name": class_name, "confidence": .9},
    ]}})
    assert len(page.images) == 1
    assert page.images[0].provenance["selected_classification"]["class_name"] == class_name


@pytest.mark.parametrize("meta", [None, {"test__description": "chart diagram flow graph"}])
def test_docling_missing_classification_does_not_infer_type_from_arbitrary_metadata(tmp_path, meta):
    page = _standardize(tmp_path, meta)
    assert len(page.images) == 1
    assert page.images[0].provenance["selected_classification"] is None


@pytest.mark.parametrize("confidence", [None, .5])
def test_docling_missing_or_tied_confidence_uses_native_first_prediction_rule(tmp_path, confidence):
    page = _standardize(tmp_path, {"classification": {"predictions": [
        {"class_name": "logo", "confidence": confidence},
        {"class_name": "bar_chart", "confidence": confidence},
    ]}})
    assert len(page.images) == 1
    assert page.images[0].provenance["selected_classification"]["class_name"] == "logo"
