from __future__ import annotations

import importlib.util
import os

import pytest

from pdf_benchmark.adapters.local import DoclingAdapter, MarkerAdapter, MinerUAdapter
from pdf_benchmark.models import RunStatus


RUN_HEAVY = os.getenv("RUN_HEAVY_LOCAL_TESTS") == "1"


@pytest.mark.heavy
@pytest.mark.skipif(not RUN_HEAVY, reason="Set RUN_HEAVY_LOCAL_TESTS=1 to run local model smoke tests")
def test_docling_minimal_pdf(minimal_pdf, tmp_path):
    if importlib.util.find_spec("docling") is None:
        pytest.skip("docling not installed")

    # Smoke-test the adapter and native Docling conversion without forcing all
    # optional enrichment models to download for a one-page synthetic PDF.
    # The full benchmark configuration is exercised later on the real corpus.
    adapter = DoclingAdapter(
        {
            "images_scale": 1.0,
            "do_ocr": False,
            "do_table_structure": True,
            "table_mode": "accurate",
            "do_formula_enrichment": False,
            "do_picture_classification": False,
            "do_chart_extraction": False,
            "generate_page_images": False,
            "generate_picture_images": False,
        }
    )
    result = adapter.convert(
        minimal_pdf,
        tmp_path / "docling",
        document_id="T01",
        raise_on_error=True,
    )
    assert result.status == RunStatus.SUCCESS
    assert result.document is not None
    assert result.document.pages
    assert (tmp_path / "docling" / "raw" / "document.json").exists()


@pytest.mark.heavy
@pytest.mark.skipif(not RUN_HEAVY, reason="Set RUN_HEAVY_LOCAL_TESTS=1 to run local model smoke tests")
def test_marker_minimal_pdf(minimal_pdf, tmp_path):
    if importlib.util.find_spec("marker") is None:
        pytest.skip("marker-pdf not installed")
    adapter = MarkerAdapter()
    result = adapter.convert(
        minimal_pdf,
        tmp_path / "marker",
        document_id="T01",
        raise_on_error=True,
    )
    assert result.status == RunStatus.SUCCESS
    assert result.document is not None
    assert result.document.pages
    assert (tmp_path / "marker" / "raw" / "marker_raw.json").exists()


@pytest.mark.heavy
@pytest.mark.skipif(not RUN_HEAVY, reason="Set RUN_HEAVY_LOCAL_TESTS=1 to run local model smoke tests")
def test_mineru_minimal_pdf(minimal_pdf, tmp_path):
    if importlib.util.find_spec("mineru") is None:
        pytest.skip("mineru not installed")
    adapter = MinerUAdapter()
    result = adapter.convert(
        minimal_pdf,
        tmp_path / "mineru",
        document_id="T01",
        raise_on_error=True,
    )
    assert result.status == RunStatus.SUCCESS
    assert result.document is not None
    assert result.document.pages
    assert (tmp_path / "mineru" / "raw" / "saved" / "structured_content.json").exists()
