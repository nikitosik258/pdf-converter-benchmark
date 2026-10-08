from __future__ import annotations

from pdf_benchmark.adapters.local import PyMuPDFAdapter
from pdf_benchmark.models import RunStatus


def test_pymupdf_minimal_pdf(minimal_pdf, tmp_path):
    adapter = PyMuPDFAdapter({"ocr_if_no_text": False})
    result = adapter.convert(minimal_pdf, tmp_path / "pymupdf", document_id="T01", raise_on_error=True)
    assert result.status == RunStatus.SUCCESS
    assert result.document is not None
    assert len(result.document.pages) == 1
    page = result.document.pages[0]
    assert any("Benchmark PDF" in b.raw_text for b in page.text_blocks)
    assert page.reading_order
    assert (tmp_path / "pymupdf" / "raw" / "pymupdf_raw.json").exists()
    assert (tmp_path / "pymupdf" / "standardized.json").exists()
