from __future__ import annotations

from pathlib import Path

from pdf_benchmark.adapters.local import MarkerAdapter
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.utils.io import write_json


def test_marker_standardize_synthetic_json(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    payload = {
        "block_type": "Document",
        "metadata": {},
        "children": [{
            "id": "/page/0/Page/0",
            "block_type": "Page",
            "bbox": [0, 0, 600, 800],
            "polygon": [[0,0],[600,0],[600,800],[0,800]],
            "html": "",
            "children": [
                {"id": "/page/0/Text/0", "block_type": "Text", "html": "<p>Hello world</p>", "bbox": [50,50,200,80], "polygon": [], "children": None, "images": {}},
                {"id": "/page/0/Equation/1", "block_type": "Equation", "html": "<math>E=mc^2</math>", "bbox": [50,100,200,130], "polygon": [], "children": None, "images": {}},
                {"id": "/page/0/Table/2", "block_type": "Table", "html": "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>", "bbox": [50,150,300,250], "polygon": [], "children": None, "images": {}},
            ],
        }],
    }
    path = write_json(raw_dir / "marker_raw.json", payload)
    result = MarkerAdapter().standardize(
        RawToolResult(primary_artifact=str(path)), raw_dir, tmp_path / "assets",
        document_id="T01", pdf_path=tmp_path / "fake.pdf",
    )
    page = result.pages[0]
    assert len(page.text_blocks) == 1
    assert len(page.formulas) == 1
    assert page.formulas[0].latex == "E=mc^2"
    assert len(page.tables) == 1
    assert page.tables[0].rows == 2
    assert page.tables[0].columns == 2
