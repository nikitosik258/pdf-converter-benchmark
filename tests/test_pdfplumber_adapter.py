from __future__ import annotations

import pytest

from pdf_benchmark.adapters.local import PdfPlumberAdapter
from pdf_benchmark.models import RunStatus


def test_pdfplumber_minimal_pdf(minimal_pdf, tmp_path):
    pytest.importorskip("pdfplumber")
    adapter = PdfPlumberAdapter()
    result = adapter.convert(minimal_pdf, tmp_path / "pdfplumber", document_id="T01", raise_on_error=True)
    assert result.status == RunStatus.SUCCESS
    assert result.document is not None
    assert len(result.document.pages) == 1
    page = result.document.pages[0]
    assert any("Benchmark" in b.raw_text for b in page.text_blocks)
    assert (tmp_path / "pdfplumber" / "raw" / "pdfplumber_raw.json").exists()
