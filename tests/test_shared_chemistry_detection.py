from pdf_benchmark.models import (
    BBox,
    ChemicalObject,
    Page,
    StandardizedDocument,
    TextBlock,
    ToolMetadata,
)
from pdf_benchmark.standardization.chemistry import (
    enrich_chemical_objects,
    linear_formula_candidates,
)


def _block(element_id: str, text: str, x1: float, y1: float, x2: float, y2: float) -> TextBlock:
    return TextBlock(
        element_id=element_id,
        page_number=1,
        raw_text=text,
        bbox=BBox(x_min=x1, y_min=y1, x_max=x2, y_max=y2),
    )


def _document(page: Page) -> StandardizedDocument:
    return StandardizedDocument(
        document_id="synthetic",
        source_pdf="synthetic.pdf",
        tool=ToolMetadata(tool_name="synthetic", distribution_name="synthetic"),
        pages=[page],
    )


def test_shared_formula_detector_handles_fragmented_indices_without_variable_false_positive():
    accepted = "FeCl 3 ·6H 2 O and FeSO 4 ·(NH 4 ) 2 SO 4 ·6H 2 O"

    assert [item[0] for item in linear_formula_candidates(accepted)] == [
        "FeCl 3 ·6H 2 O",
        "FeSO 4 ·(NH 4 ) 2 SO 4 ·6H 2 O",
    ]
    assert linear_formula_candidates(
        "PDF D05; variables U₁₁ B, U0B, SV0, Y2KC, 2KC, КС1, C112O, P1 P4 and P1P2"
    ) == []


def test_word_ocr_structures_require_explicit_table_headers_and_form_rows():
    blocks = [
        _block("title-1", "Структурные", 0.35, 0.10, 0.46, 0.12),
        _block("title-2", "формулы", 0.47, 0.10, 0.54, 0.12),
        _block("title-3", "красителей", 0.55, 0.10, 0.64, 0.12),
        _block("h-name", "Название", 0.10, 0.20, 0.20, 0.22),
        _block("h-structure", "Структурная", 0.33, 0.20, 0.43, 0.22),
        _block("h-formula", "формула", 0.44, 0.20, 0.51, 0.22),
        _block("h-pka", "pKa", 0.62, 0.20, 0.66, 0.22),
    ]
    for index, y in enumerate((0.27, 0.37, 0.47, 0.57), start=1):
        blocks.extend(
            [
                _block(f"name-{index}", f"Dye {index}", 0.10, y, 0.20, y + 0.02),
                _block(f"label-a-{index}", "NaO3S", 0.34, y, 0.40, y + 0.02),
                _block(f"label-b-{index}", "N=N", 0.44, y + 0.01, 0.48, y + 0.03),
            ]
        )
    page = Page(page_number=1, width=1000, height=1000, text_blocks=blocks)

    enriched = enrich_chemical_objects(_document(page))
    structures = [item for item in enriched.pages[0].chemical_objects if item.subtype == "structure"]

    assert len(structures) == 4
    assert all(item.provenance["gt_used"] is False for item in structures)
    assert all(item.provenance["source"] == "spatial_structure_labels_under_explicit_header" for item in structures)
    assert structures[0].bbox.y_min >= 0.22


def test_adapter_native_chemistry_is_preserved_without_same_subtype_duplicates():
    native_linear = ChemicalObject(
        element_id="native-linear",
        page_number=1,
        subtype="linear_formula",
        raw_formula="H2SO4",
        provenance={"source": "provider_native"},
    )
    native_structure = ChemicalObject(
        element_id="native-structure",
        page_number=1,
        subtype="structure",
        text_labels=["N=N"],
        provenance={"source": "provider_native"},
    )
    page = Page(
        page_number=1,
        width=1000,
        height=1000,
        text_blocks=[_block("text", "H2O and FeCl3", 0.1, 0.1, 0.3, 0.12)],
        chemical_objects=[native_linear, native_structure],
    )

    enriched = enrich_chemical_objects(_document(page))

    assert [item.element_id for item in enriched.pages[0].chemical_objects] == [
        "native-linear",
        "native-structure",
    ]
    assert enriched.metadata["chemistry_enrichment"] == {
        "detector_version": "gt_independent_chemistry_v1",
        "gt_used": False,
        "linear_added": 0,
        "structures_added": 0,
    }
