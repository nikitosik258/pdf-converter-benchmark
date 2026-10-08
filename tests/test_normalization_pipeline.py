from pdf_benchmark.models import (
    Caption,
    ChemicalObject,
    DiagramObject,
    Formula,
    Page,
    StandardizedDocument,
    Table,
    TableCell,
    TextBlock,
    ToolMetadata,
)
from pdf_benchmark.normalization.pipeline import (
    NormalizationConfig,
    normalize_document,
)


def _document():
    return StandardizedDocument(
        document_id="DTEST",
        source_pdf="test.pdf",
        tool=ToolMetadata(
            tool_name="fake",
            distribution_name="fake",
            version="1",
        ),
        pages=[
            Page(
                page_number=1,
                width=595,
                height=842,
                text_blocks=[
                    TextBlock(
                        element_id="txt1",
                        page_number=1,
                        raw_text="«Текст»\nпродолжается",
                    )
                ],
                formulas=[
                    Formula(
                        element_id="m1",
                        page_number=1,
                        latex=r"\dfrac{α^2}{ y }",
                    )
                ],
                chemical_objects=[
                    ChemicalObject(
                        element_id="c1",
                        page_number=1,
                        subtype="linear_formula",
                        raw_formula="Fe₃O₄",
                    )
                ],
                tables=[
                    Table(
                        element_id="t1",
                        page_number=1,
                        rows=1,
                        columns=1,
                        cells=[TableCell(row_index=0, column_index=0, text=" a  b ")],
                        caption=Caption(text="Таблица  1"),
                    )
                ],
                diagrams=[
                    DiagramObject(
                        element_id="d1",
                        page_number=1,
                        text_elements=["Input  Layer"],
                        key_elements=["Dense"],
                        caption=Caption(text="Рис.  1"),
                    )
                ],
            )
        ],
    )


def test_pipeline_populates_normalized_fields_without_overwriting_raw():
    raw = _document()
    normalized = normalize_document(raw, NormalizationConfig())

    assert raw.pages[0].text_blocks[0].normalized_text is None
    assert normalized.pages[0].text_blocks[0].raw_text == "«Текст»\nпродолжается"
    assert normalized.pages[0].text_blocks[0].normalized_text == '"Текст" продолжается'

    assert normalized.pages[0].formulas[0].latex == r"\dfrac{α^2}{ y }"
    assert normalized.pages[0].formulas[0].normalized_latex == r"\frac{\alpha^{2}}{y}"

    assert normalized.pages[0].chemical_objects[0].normalized_formula == "Fe3O4"
    assert normalized.pages[0].tables[0].cells[0].normalized_text == "a b"
    assert normalized.pages[0].tables[0].caption.normalized_text == "Таблица 1"
    assert normalized.pages[0].diagrams[0].caption.normalized_text == "Рис. 1"

    assert normalized.metadata["normalization"]["version"] == "1.0"
    assert len(normalized.metadata["normalization"]["config_sha256"]) == 64


def test_config_fingerprint_is_reproducible():
    a = NormalizationConfig()
    b = NormalizationConfig()
    assert a.fingerprint() == b.fingerprint()
