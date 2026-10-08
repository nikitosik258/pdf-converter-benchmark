"""Regression tests for the native coordinate canvas, without vendor calls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_benchmark.adapters.base import ToolOutputParseError
from pdf_benchmark.adapters.cloud.nutrient_adapter import NutrientDataExtractionAdapter
from pdf_benchmark.models import RawToolResult


def _standardize(tmp_path, minimal_pdf, elements):
    response = tmp_path / "response.json"
    response.write_text(json.dumps({"output": {"elements": elements}}), encoding="utf-8")
    return NutrientDataExtractionAdapter().standardize(
        RawToolResult(primary_artifact=str(response)),
        tmp_path,
        tmp_path / "assets",
        document_id="T01",
        pdf_path=minimal_pdf,
    )


def _coords(box):
    assert box is not None
    return box.x_min, box.y_min, box.x_max, box.y_max


def test_nutrient_d04_native_bounds_use_provider_canvas(tmp_path, minimal_pdf):
    # Dimensions/bounds from the saved D04 response; text is irrelevant here.
    element = {
        "type": "paragraph", "text": "Coordinate regression", "id": "d04_sample",
        "page": {"pageIndex": 0, "pageNumber": 1, "width": 1653, "height": 2339},
        "bounds": {"height": 72, "width": 442.50146, "x": 891, "y": 300},
    }
    page = _standardize(tmp_path, minimal_pdf, [element]).pages[0]
    assert (page.width, page.height) == (595, 842)
    assert _coords(page.text_blocks[0].bbox) == pytest.approx(
        (891 / 1653, 300 / 2339, 1333.50146 / 1653, 372 / 2339)
    )


@pytest.mark.parametrize("coordinate_mode", ["provider", "legacy_pdf", "normalized"])
def test_nutrient_coordinate_units_apply_to_objects_cells_and_captions(
    tmp_path, minimal_pdf, coordinate_mode,
):
    fixture = Path(__file__).parent / "fixtures/cloud/nutrient.json"
    elements = json.loads(fixture.read_text(encoding="utf-8"))["output"]["elements"]
    width, height = {"provider": (1653, 2339), "legacy_pdf": (595, 842),
                     "normalized": (1, 1)}[coordinate_mode]

    def bounds(x, y, w, h):
        return {"x": x*width, "y": y*height, "width": w*width, "height": h*height}

    for element in elements:
        if coordinate_mode != "legacy_pdf":
            element["page"].update(width=1653, height=2339)
        element["bounds"] = bounds(.1, .2, .4, .2)
        if element["type"] in {"table", "picture", "chart"}:
            element["caption"] = {"text": "Caption", "bounds": bounds(.1, .41, .4, .05)}
        for cell in element.get("cells", []):
            cell["bounds"] = bounds(.1 + .2*cell["column"], .2 + .1*cell["row"], .2, .1)

    page = _standardize(tmp_path, minimal_pdf, elements).pages[0]
    assert (page.width, page.height) == (595, 842)
    objects = [*page.text_blocks, *page.tables, *page.formulas, *page.images, *page.diagrams]
    assert len(objects) == 6
    for obj in objects:
        assert _coords(obj.bbox) == pytest.approx((.1, .2, .5, .4)), obj.element_id
    for cell in page.tables[0].cells:
        x, y = .1 + .2*cell.column_index, .2 + .1*cell.row_index
        assert _coords(cell.bbox) == pytest.approx((x, y, x + .2, y + .1))
    for obj in [page.tables[0], page.images[0], page.diagrams[0]]:
        assert _coords(obj.caption.bbox) == pytest.approx((.1, .41, .5, .46))


def test_nutrient_uses_each_pages_native_dimensions(tmp_path, minimal_pdf, monkeypatch):
    monkeypatch.setattr(
        "pdf_benchmark.adapters.cloud.nutrient_adapter.pdf_page_sizes",
        lambda _: [(595, 842), (612, 792)],
    )
    elements = [
        {"type": "paragraph", "text": "Text", "id": f"p{number}",
         "page": {"pageNumber": number, "width": width, "height": height},
         "bounds": {"x": .1*width, "y": .2*height, "width": .4*width, "height": .2*height}}
        for number, width, height in [(1, 1653, 2339), (2, 1800, 2400)]
    ]
    doc = _standardize(tmp_path, minimal_pdf, elements)
    assert [(p.width, p.height) for p in doc.pages] == [(595, 842), (612, 792)]
    for page in doc.pages:
        assert _coords(page.text_blocks[0].bbox) == pytest.approx((.1, .2, .5, .4))


@pytest.mark.parametrize("width,height", [
    (0, 2339), (1653, -1), ("NaN", 2339), (1653, "Infinity"),
    ("invalid", 2339), (None, 2339), (1653, None),
])
def test_nutrient_invalid_native_canvas_is_not_silently_replaced_by_pdf_units(
    tmp_path, minimal_pdf, width, height,
):
    element = {
        "type": "paragraph", "text": "Text", "id": "invalid_canvas",
        "page": {"pageNumber": 1, "width": width, "height": height},
        "bounds": {"x": 891, "y": 300, "width": 442, "height": 72},
    }
    with pytest.raises(ToolOutputParseError, match="coordinate canvas"):
        _standardize(tmp_path, minimal_pdf, [element])
