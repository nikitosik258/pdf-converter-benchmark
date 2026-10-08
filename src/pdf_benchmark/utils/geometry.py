from __future__ import annotations

from typing import Iterable

from pdf_benchmark.models import BBox


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def normalize_bbox(
    bbox: Iterable[float] | None,
    width: float,
    height: float,
    *,
    origin: str = "top-left",
) -> BBox | None:
    if bbox is None or width <= 0 or height <= 0:
        return None
    vals = list(bbox)
    if len(vals) < 4:
        return None
    x0, y0, x1, y1 = map(float, vals[:4])
    if origin.lower().replace("_", "-") in {"bottom-left", "bottomleft"}:
        y0, y1 = height - y1, height - y0
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    return BBox(
        x_min=_clamp01(xa / width),
        y_min=_clamp01(ya / height),
        x_max=_clamp01(xb / width),
        y_max=_clamp01(yb / height),
    )


def normalize_1000_bbox(bbox: object) -> BBox | None:
    if bbox is None:
        return None
    if isinstance(bbox, dict):
        bbox = bbox.get("normalized") or bbox.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    return normalize_bbox(bbox[:4], 1000.0, 1000.0)


def bbox_sort_key(bbox: BBox | None, fallback: int = 0) -> tuple[float, float, int]:
    if bbox is None:
        return (10.0, 10.0, fallback)
    return (bbox.y_min, bbox.x_min, fallback)
