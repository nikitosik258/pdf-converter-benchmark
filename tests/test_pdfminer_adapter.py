from pathlib import Path

import pymupdf

from pdf_benchmark.adapters.local.pdfminer_adapter import PdfMinerAdapter


def test_pdfminer_minimal_pdf(tmp_path: Path):
    pdf_path = tmp_path / "minimal.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "pdfminer smoke test")
    doc.save(pdf_path)
    doc.close()

    result = PdfMinerAdapter().convert(
        pdf_path,
        tmp_path / "pdfminer",
        document_id="T01",
        raise_on_error=True,
    )

    assert str(result.status).lower().endswith("success")
    assert result.document is not None
    assert len(result.document.pages) == 1
    assert result.document.pages[0].text_blocks
    assert "pdfminer" in result.document.pages[0].text_blocks[0].raw_text.lower()
