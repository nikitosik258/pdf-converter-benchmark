from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from pdf_benchmark.models import BBox


def pdf_page_count(path: Path) -> int:
    try:
        import fitz
        with fitz.open(path) as doc:
            return len(doc)
    except Exception as exc:
        raise RuntimeError("PyMuPDF is required in the cloud environment for deterministic page counting") from exc


def split_pdf(path: Path, out_dir: Path, pages_per_chunk: int) -> list[tuple[Path, int, int]]:
    import fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, int, int]] = []
    with fitz.open(path) as src:
        for start in range(0, len(src), pages_per_chunk):
            end = min(len(src), start + pages_per_chunk)
            dst = fitz.open()
            dst.insert_pdf(src, from_page=start, to_page=end - 1)
            chunk = out_dir / f"pages_{start+1:04d}_{end:04d}.pdf"
            dst.save(chunk, garbage=4, deflate=True)
            dst.close()
            chunks.append((chunk, start, end - start))
    return chunks


def clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def bbox_from_normalized_vertices(vertices: Iterable[Any] | None) -> BBox | None:
    pts = list(vertices or [])
    if not pts:
        return None
    xs, ys = [], []
    for p in pts:
        if isinstance(p, dict):
            x, y = p.get("x", 0), p.get("y", 0)
        else:
            x, y = getattr(p, "x", 0), getattr(p, "y", 0)
        xs.append(float(x or 0)); ys.append(float(y or 0))
    return BBox(x_min=clamp(min(xs)), y_min=clamp(min(ys)), x_max=clamp(max(xs)), y_max=clamp(max(ys)))


def bbox_from_polygon(polygon: Iterable[Any] | None, width: float, height: float) -> BBox | None:
    pts = list(polygon or [])
    if not pts or width <= 0 or height <= 0:
        return None
    xs, ys = [], []
    if all(isinstance(v, (int, float)) for v in pts):
        vals = [float(v) for v in pts]
        xs, ys = vals[0::2], vals[1::2]
    else:
        for p in pts:
            if isinstance(p, dict):
                xs.append(float(p.get("x", 0))); ys.append(float(p.get("y", 0)))
            else:
                xs.append(float(getattr(p, "x", 0))); ys.append(float(getattr(p, "y", 0)))
    return BBox(
        x_min=clamp(min(xs) / width), y_min=clamp(min(ys) / height),
        x_max=clamp(max(xs) / width), y_max=clamp(max(ys) / height),
    )


def text_anchor(document_text: str, anchor: dict[str, Any] | None) -> str:
    if not anchor:
        return ""
    chunks = []
    for seg in anchor.get("textSegments", anchor.get("text_segments", [])) or []:
        start = int(seg.get("startIndex", seg.get("start_index", 0)) or 0)
        end = int(seg.get("endIndex", seg.get("end_index", 0)) or 0)
        chunks.append(document_text[start:end])
    return "".join(chunks)


def adobe_path_kind(path: str) -> str:
    value = path.lower()
    if "/table" in value:
        return "table"
    if "/figure" in value or "/aside" in value:
        return "figure"
    if re.search(r"/(h\d|title)(\[|/|$)", value):
        return "heading"
    if "/li" in value:
        return "list_item"
    if "/caption" in value:
        return "caption"
    return "paragraph"


def pdf_page_sizes(path: Path) -> list[tuple[float, float]]:
    """Return PDF page sizes in native PDF points."""
    try:
        import fitz
        with fitz.open(path) as doc:
            return [(float(p.rect.width), float(p.rect.height)) for p in doc]
    except Exception as exc:
        raise RuntimeError("PyMuPDF is required in the cloud environment for page geometry") from exc


def bbox_from_normalized_polygon(polygon: Iterable[Any] | None) -> BBox | None:
    """Accept Mindee-style normalized polygons in dict/object or [x, y] point form."""
    pts = list(polygon or [])
    if not pts:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for p in pts:
        if isinstance(p, dict):
            x, y = p.get("x", 0), p.get("y", 0)
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = p[0], p[1]
        else:
            x, y = getattr(p, "x", 0), getattr(p, "y", 0)
        xs.append(float(x or 0))
        ys.append(float(y or 0))
    return BBox(
        x_min=clamp(min(xs)), y_min=clamp(min(ys)),
        x_max=clamp(max(xs)), y_max=clamp(max(ys)),
    )
