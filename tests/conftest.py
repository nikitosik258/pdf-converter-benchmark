from __future__ import annotations

from pathlib import Path

import pytest


def _build_minimal_pdf() -> bytes:
    """Create a small valid one-page PDF using only the Python standard library.

    This fixture intentionally has no dependency on PyMuPDF so the same test PDF
    can be created inside the isolated Marker and MinerU environments.
    """
    content = (
        b"BT\n"
        b"/F1 16 Tf\n"
        b"72 770 Td\n"
        b"(Benchmark PDF) Tj\n"
        b"0 -28 Td\n"
        b"/F1 10 Tf\n"
        b"(Technical text with E = mc^2 and value 12.5.) Tj\n"
        b"0 -38 Td\n"
        b"(Name        Value) Tj\n"
        b"0 -18 Td\n"
        b"(alpha       42) Tj\n"
        b"0 -52 Td\n"
        b"(Figure 1. Placeholder caption) Tj\n"
        b"ET\n"
        b"72 650 258 80 re S\n"
        b"72 690 m 330 690 l S\n"
        b"180 650 m 180 730 l S\n"
    )

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content
        + b"endstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]

    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        (
            f"trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n"
            f"{xref_offset}\n"
            f"%%EOF\n"
        ).encode("ascii")
    )
    return bytes(pdf)


@pytest.fixture()
def minimal_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "minimal.pdf"
    path.write_bytes(_build_minimal_pdf())
    return path
