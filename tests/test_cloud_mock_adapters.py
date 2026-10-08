from pathlib import Path

import pytest

from pdf_benchmark.adapters.cloud import (
    OCRSpaceAdapter,
    NutrientDataExtractionAdapter,
    MindeeOCRAdapter,
    AdobeExtractAdapter,
    AzureDocumentIntelligenceAdapter,
    LlamaParseAdapter,
)
from pdf_benchmark.models import RunStatus

FIX = Path(__file__).parent / "fixtures" / "cloud"

CASES = [
    (OCRSpaceAdapter, "ocr_space.json"),
    (NutrientDataExtractionAdapter, "nutrient.json"),
    (MindeeOCRAdapter, "mindee.json"),
    (AdobeExtractAdapter, "adobe.json"),
    (LlamaParseAdapter, "llamaparse.json"),
    (AzureDocumentIntelligenceAdapter, "azure.json"),
]


@pytest.mark.parametrize("adapter_cls,fixture", CASES)
def test_cloud_adapter_mock_has_zero_network_calls(adapter_cls, fixture, minimal_pdf, tmp_path):
    adapter = adapter_cls({"mock": True, "mock_fixture": str(FIX / fixture)})
    result = adapter.convert(
        minimal_pdf,
        tmp_path / adapter.tool_name,
        document_id="T01",
        raise_on_error=True,
    )
    assert result.status == RunStatus.SUCCESS
    assert result.document is not None
    assert result.document.pages
    assert result.raw_result is not None
    assert result.raw_result.metadata["mock"] is True
    assert result.raw_result.metadata["network_calls"] == 0
    assert result.raw_result.metadata["estimated_cost_usd"] == 0.0
    assert (tmp_path / adapter.tool_name / "standardized.json").exists()
    assert (tmp_path / adapter.tool_name / "raw" / "mock_response.json").exists()


def test_llamaparse_mock_exposes_structured_categories(minimal_pdf, tmp_path):
    adapter = LlamaParseAdapter({"mock": True, "mock_fixture": str(FIX / "llamaparse.json")})
    result = adapter.convert(minimal_pdf, tmp_path / "llamaparse_structured", document_id="T02", raise_on_error=True)
    page = result.document.pages[0]
    assert len(page.text_blocks) >= 1
    assert len(page.tables) == 1
    assert len(page.formulas) == 1
    assert len(page.images) == 1
    assert len(page.diagrams) == 1
    assert len(page.chemical_objects) == 0
