"""Saved MinerU asset/caption handling without importing or running MinerU."""
from __future__ import annotations

import base64

import pytest

from pdf_benchmark.adapters.local import MinerUAdapter
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.utils.io import write_json


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1cAAAAASUVORK5CYII="
)


def _standardize(tmp_path, block, *, asset_state="file"):
    saved = tmp_path / "raw" / "saved"
    (saved / "images").mkdir(parents=True)
    if asset_state == "file":
        (saved / "images/figure.png").write_bytes(PNG)
    elif asset_state == "directory":
        (saved / "images/figure.png").mkdir()
    path = write_json(saved / "structured_content.json", {
        "pages": [{"page_idx": 0, "blocks": [block]}],
    })
    assets = tmp_path / "assets"
    assets.mkdir()
    doc = MinerUAdapter().standardize(
        RawToolResult(primary_artifact=str(path), metadata={"saved_dir": str(saved)}),
        saved.parent, assets, document_id="T01", pdf_path=tmp_path / "unused.pdf",
    )
    page = doc.pages[0]
    obj = [*page.images, *page.diagrams, *page.tables][0]
    assert page.reading_order == [obj.element_id]
    assert (obj.bbox.x_min, obj.bbox.y_min, obj.bbox.x_max, obj.bbox.y_max) == (.1, .2, .8, .7)
    return obj


@pytest.mark.parametrize("kind", ["image", "chart"])
@pytest.mark.parametrize("source_format", ["native_string", "native_dict", "nested_string", "nested_dict", "legacy_path"])
def test_mineru_copies_native_and_legacy_visual_assets_with_ordered_captions(tmp_path, kind, source_format):
    block = {"type": kind, "bbox": [.1, .2, .8, .7], "content": "",
             "captions": [{"content": "English caption", "bbox": [.1, .7, .8, .75]},
                          {"content": "Русская подпись", "bbox": [.1, .75, .8, .8]}]}
    source = "images/figure.png"
    if source_format == "native_string": block["image_source"] = source
    elif source_format == "native_dict": block["image_source"] = {"path": source}
    elif source_format == "nested_string": block["content"] = {"image_source": source}
    elif source_format == "nested_dict": block["content"] = {"image_source": {"path": source}}
    else: block["image_path"] = source
    obj = _standardize(tmp_path, block)
    assert obj.asset_path is not None
    assert (tmp_path / obj.asset_path).read_bytes() == PNG
    assert obj.caption.text == "English caption Русская подпись"
    if kind == "image": assert obj.extraction_success


@pytest.mark.parametrize("kind", ["image", "chart"])
@pytest.mark.parametrize("asset_state", ["missing", "directory"])
def test_mineru_missing_asset_does_not_claim_success_or_drop_caption(tmp_path, kind, asset_state):
    obj = _standardize(tmp_path, {
        "type": kind, "bbox": [.1, .2, .8, .7], "content": "",
        "image_source": "images/figure.png", "captions": [{"content": "Caption"}],
    }, asset_state=asset_state)
    assert obj.asset_path is None
    assert obj.caption.text == "Caption"
    if kind == "image": assert not obj.extraction_success


def test_mineru_plural_caption_prevents_duplicate_legacy_alias(tmp_path):
    obj = _standardize(tmp_path, {
        "type": "image", "bbox": [.1, .2, .8, .7], "content": "",
        "captions": [{"content": "Repeated caption"}], "image_caption": "Repeated caption",
    })
    assert obj.caption.text == "Repeated caption"


def test_mineru_caption_metadata_is_not_read_as_text_and_legacy_fallback_remains(tmp_path):
    obj = _standardize(tmp_path, {
        "type": "image", "bbox": [.1, .2, .8, .7],
        "content": {"image_caption": ["Legacy", "caption"]},
        "captions": [{"bbox": [1, 2, 3, 4], "confidence": .9}],
    })
    assert obj.caption.text == "Legacy caption"


def test_mineru_empty_captions_remain_absent(tmp_path):
    obj = _standardize(tmp_path, {
        "type": "image", "bbox": [.1, .2, .8, .7], "content": "",
        "captions": [{"content": "", "bbox": [1, 2, 3, 4]}],
    })
    assert obj.caption is None


def test_mineru_shared_caption_field_preserves_table_structure(tmp_path):
    obj = _standardize(tmp_path, {
        "type": "table", "bbox": [.1, .2, .8, .7],
        "content": "| A | B |\n| --- | --- |\n| 1 | 2 |",
        "captions": [{"content": "Table 1"}, {"content": "Таблица 1"}],
    })
    assert (obj.rows, obj.columns, len(obj.cells)) == (2, 2, 4)
    assert [c.text for c in obj.cells] == ["A", "B", "1", "2"]
    assert obj.caption.text == "Table 1 Таблица 1"
