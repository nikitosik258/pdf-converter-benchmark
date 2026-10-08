"""Native picture text survives standardization; no Docling inference."""
import pytest

pytest.importorskip("docling_core")
from docling_core.types.doc import DoclingDocument
from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
from docling_core.types.doc.common.reference import ProvenanceItem
from docling_core.types.doc.items.picture.meta import PictureMeta
from docling_core.types.doc.labels import DocItemLabel

from pdf_benchmark.adapters.local import DoclingAdapter
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.utils.io import write_json


def native_document(class_name="line_chart"):
    doc = DoclingDocument(name="picture_text")
    doc.add_page(page_no=1, size=Size(width=100, height=100))
    prov = ProvenanceItem(page_no=1, charspan=(0, 0), bbox=BoundingBox(
        l=10, t=10, r=90, b=90, coord_origin=CoordOrigin.TOPLEFT,
    ))
    picture = doc.add_picture(prov=prov)
    picture.meta = PictureMeta.model_validate({"classification": {"predictions": [
        {"class_name": class_name, "confidence": .9},
    ]}})
    return doc, picture, prov


def standardize(tmp_path, doc):
    raw = tmp_path / "raw"
    path = write_json(raw / "document.json", doc.export_to_dict())
    assets = tmp_path / "assets"
    assets.mkdir()
    return DoclingAdapter().standardize(
        RawToolResult(primary_artifact=str(path)), raw, assets,
        document_id="T01", pdf_path=tmp_path / "unused.pdf",
    ).pages[0]


@pytest.mark.parametrize("class_name", ["line_chart", "photograph"])
def test_picture_text_follows_native_refs_and_keeps_repeated_labels(tmp_path, class_name):
    doc, picture, prov = native_document(class_name)
    first = doc.add_text(label=DocItemLabel.TEXT, text="  Same\nlabel ", parent=picture, prov=prov)
    second = doc.add_text(label=DocItemLabel.TEXT, text="Same label", parent=picture, prov=prov)
    # Native order, not text-array order or lexical order, determines output.
    picture.children.reverse()
    doc.add_text(label=DocItemLabel.TEXT, text="outside", prov=prov)
    page = standardize(tmp_path, doc)
    obj = (page.diagrams or page.images)[0]
    assert obj.provenance["picture_text_elements"] == [
        {"source_element_id": second.self_ref, "text": "Same label"},
        {"source_element_id": first.self_ref, "text": "Same label"},
    ]
    if page.diagrams:
        assert obj.text_elements == ["Same label", "Same label"]
        assert obj.key_elements == []  # OCR labels are not inferred graph structure.
    assert [b.raw_text for b in page.text_blocks] == ["outside"]
    assert page.reading_order == [picture.self_ref, "#/texts/2"]


def test_picture_text_excludes_captions_footnotes_and_empty_text(tmp_path):
    doc, picture, prov = native_document()
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Figure 1", parent=picture, prov=prov)
    picture.captions.append(caption.get_ref())
    footnote = doc.add_text(label=DocItemLabel.FOOTNOTE, text="Source note", parent=picture, prov=prov)
    picture.footnotes.append(footnote.get_ref())
    doc.add_text(label=DocItemLabel.TEXT, text="  ", parent=picture, prov=prov)
    text = doc.add_text(label=DocItemLabel.TEXT, text="axis", parent=picture, prov=prov)
    page = standardize(tmp_path, doc)
    obj = page.diagrams[0]
    assert obj.text_elements == ["axis"]
    assert obj.caption.text == "Figure 1"
    assert obj.provenance["picture_text_elements"] == [{"source_element_id": text.self_ref, "text": "axis"}]
    assert [b.raw_text for b in page.text_blocks] == ["Figure 1"]


def test_picture_text_walks_groups_without_taking_text_from_nested_picture(tmp_path):
    doc, picture, prov = native_document()
    group = doc.add_group(parent=picture)
    text = doc.add_text(label=DocItemLabel.TEXT, text="grouped axis", parent=group, prov=prov)
    nested = doc.add_picture(parent=group, prov=prov)
    doc.add_text(label=DocItemLabel.TEXT, text="other picture", parent=nested, prov=prov)
    obj = standardize(tmp_path, doc).diagrams[0]
    assert obj.text_elements == ["grouped axis"]
    assert obj.provenance["picture_text_elements"][0]["source_element_id"] == text.self_ref


def test_picture_without_child_text_remains_empty(tmp_path):
    doc, _, _ = native_document()
    obj = standardize(tmp_path, doc).diagrams[0]
    assert obj.text_elements == []
    assert obj.provenance["picture_text_elements"] == []
