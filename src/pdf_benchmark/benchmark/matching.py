from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from pdf_benchmark.evaluation.common import bbox_iou, normalized_edit_similarity
from pdf_benchmark.models import (
    BBox,
    ChemicalObject,
    DiagramObject,
    Formula,
    ImageObject,
    Page,
    StandardizedDocument,
    Table,
    TextBlock,
)
from pdf_benchmark.normalization.chemistry import normalize_chemical_formula
from pdf_benchmark.normalization.latex import normalize_latex
from pdf_benchmark.normalization.text import normalize_text

from .ground_truth import GroundTruthObject


class MatchingConfig(BaseModel):
    spatial_min_iou: float = Field(default=0.10, ge=0.0, le=1.0)
    content_min_similarity: float = Field(default=0.25, ge=0.0, le=1.0)
    # Text parsers have very different granularity: one tool may emit a whole
    # page, another a paragraph, another individual lines/words. For text GT
    # regions, select all blocks whose overlap with the GT is large relative to
    # the smaller of the two boxes, then aggregate them before evaluation.
    text_multiblock_min_overlap: float = Field(default=0.50, ge=0.0, le=1.0)
    visual_detection_policy: Literal[
        "sampled_gt_recall_v1",
        "exhaustive_page_f1_v1",
    ] = "sampled_gt_recall_v1"


@dataclass
class Candidate:
    element_id: str
    object_type: str
    page: int
    bbox: BBox | None
    payload: dict[str, Any]


class MatchRecord(BaseModel):
    object_id: str
    document_id: str
    page: int
    object_type: str
    prediction_element_id: str | None = None
    matched: bool
    match_method: str
    match_score: float = Field(ge=0.0, le=1.0)
    reference: dict[str, Any]
    prediction: dict[str, Any] | None = None
    context: dict[str, Any] = Field(default_factory=dict)


def _caption_payload(obj: Any) -> dict[str, Any] | None:
    caption = getattr(obj, "caption", None)
    return caption.model_dump(mode="json") if caption is not None else None


def _candidate_from_model(obj: Any, object_type: str) -> Candidate:
    payload = obj.model_dump(mode="json")
    if object_type == "text":
        payload["text"] = obj.normalized_text if obj.normalized_text is not None else obj.raw_text
    elif object_type == "math_formula":
        # Preserve the adapter's source representation. Matching may use the
        # normalized field, but raw_exact_match must receive the original.
        payload["latex"] = obj.latex if obj.latex is not None else (obj.raw_text or "")
    elif object_type == "chemical_formula":
        payload["formula"] = (
            obj.normalized_formula
            if obj.normalized_formula is not None
            else (obj.raw_formula or "")
        )
    elif object_type in {"image", "diagram"}:
        payload["caption"] = _caption_payload(obj)
    return Candidate(
        element_id=obj.element_id,
        object_type=object_type,
        page=obj.page_number,
        bbox=obj.bbox,
        payload=payload,
    )


def page_candidates(page: Page, object_type: str) -> list[Candidate]:
    if object_type == "text":
        return [_candidate_from_model(x, object_type) for x in page.text_blocks]
    if object_type == "table":
        return [_candidate_from_model(x, object_type) for x in page.tables]
    if object_type == "math_formula":
        return [_candidate_from_model(x, object_type) for x in page.formulas]
    if object_type == "chemical_formula":
        return [
            _candidate_from_model(x, object_type)
            for x in page.chemical_objects
            if x.subtype == "linear_formula"
        ]
    if object_type == "chemical_structure":
        return [
            _candidate_from_model(x, object_type)
            for x in page.chemical_objects
            if x.subtype == "structure"
        ]
    if object_type == "image":
        return [_candidate_from_model(x, object_type) for x in page.images]
    if object_type == "diagram":
        return [_candidate_from_model(x, object_type) for x in page.diagrams]
    raise ValueError(f"Unsupported object type: {object_type}")


def _gt_text(gt: GroundTruthObject) -> str:
    ref = gt.reference
    if gt.object_type == "text":
        return normalize_text(str(ref.get("text", ref.get("normalized_text", ref.get("raw_text", "")))))
    if gt.object_type == "math_formula":
        return normalize_latex(str(ref.get("latex", ref.get("normalized_latex", "")) or ""))
    if gt.object_type == "chemical_formula":
        return normalize_chemical_formula(
            str(ref.get("formula", ref.get("normalized_formula", ref.get("raw_formula", ""))) or "")
        )
    if gt.object_type == "table":
        cells = ref.get("cells") or []
        return normalize_text(
            " ".join(str(cell.get("normalized_text", cell.get("text", ""))) for cell in cells),
            preserve_paragraphs=False,
        )
    return ""


def _pred_text(candidate: Candidate) -> str:
    payload = candidate.payload
    if candidate.object_type == "text":
        return normalize_text(str(payload.get("text", "")))
    if candidate.object_type == "math_formula":
        normalized = payload.get("normalized_latex")
        if normalized is not None:
            return str(normalized)
        return normalize_latex(
            str(payload.get("latex", payload.get("raw_text", "")) or "")
        )
    if candidate.object_type == "chemical_formula":
        return normalize_chemical_formula(str(payload.get("formula", "") or ""))
    if candidate.object_type == "table":
        cells = payload.get("cells") or []
        return normalize_text(
            " ".join(str(cell.get("normalized_text", cell.get("text", ""))) for cell in cells),
            preserve_paragraphs=False,
        )
    return ""



def _bbox_area(box: BBox) -> float:
    return max(0.0, box.x_max - box.x_min) * max(0.0, box.y_max - box.y_min)


def _bbox_intersection_over_smaller(a: BBox, b: BBox) -> float:
    """Intersection area divided by the smaller box area.

    IoU is unsuitable for text-granularity matching: a line fully contained in
    a large GT paragraph has very low IoU even though it is spatially relevant.
    Intersection-over-smaller is near 1 both when a small line is inside a GT
    region and when a coarse page-level OCR block contains the GT region.
    """
    ix0 = max(a.x_min, b.x_min)
    iy0 = max(a.y_min, b.y_min)
    ix1 = min(a.x_max, b.x_max)
    iy1 = min(a.y_max, b.y_max)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    intersection = iw * ih

    denom = min(_bbox_area(a), _bbox_area(b))
    if denom <= 0:
        return 0.0
    return intersection / denom


def _union_bbox(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return BBox(
        x_min=min(b.x_min for b in boxes),
        y_min=min(b.y_min for b in boxes),
        x_max=max(b.x_max for b in boxes),
        y_max=max(b.y_max for b in boxes),
    )


def _deduplicate_text_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Reserve physical duplicates together, including before Hungarian assigns them."""
    seen: set[tuple[str, float, float, float, float]] = set()
    result = []
    for candidate in candidates:
        box = candidate.bbox
        if box is not None:
            key = (_pred_text(candidate), box.x_min, box.y_min, box.x_max, box.y_max)
            if key in seen:
                continue
            seen.add(key)
        result.append(candidate)
    return result


def _bbox_contains(outer: BBox, inner: BBox) -> bool:
    return (outer.x_min <= inner.x_min and outer.y_min <= inner.y_min
            and outer.x_max >= inner.x_max and outer.y_max >= inner.y_max)


def _extends_text_anchor(region: BBox, anchor: BBox, fragment: BBox) -> bool:
    # Only the fragment's area inside the GT region is relevant. Nested blocks
    # do not prove that the anchor omitted a separate part of the region.
    x0, y0 = max(region.x_min, fragment.x_min), max(region.y_min, fragment.y_min)
    x1, y1 = min(region.x_max, fragment.x_max), min(region.y_max, fragment.y_max)
    return (x1 > x0 and y1 > y0
            and (x0 < anchor.x_min or y0 < anchor.y_min
                 or x1 > anchor.x_max or y1 > anchor.y_max))


def _aggregate_text_candidate(
    gt: GroundTruthObject,
    page: Page,
    candidates: list[Candidate],
    cfg: MatchingConfig,
) -> tuple[Candidate | None, float, dict[str, Any]]:
    """Build one synthetic prediction from all spatially relevant text blocks."""
    if gt.bbox is None or not candidates:
        return None, 0.0, {}

    order_map = {eid: idx for idx, eid in enumerate(page.reading_order)}

    selected: list[tuple[Candidate, float]] = []
    for candidate in candidates:
        if candidate.bbox is None:
            continue
        overlap = _bbox_intersection_over_smaller(gt.bbox, candidate.bbox)
        if overlap >= cfg.text_multiblock_min_overlap:
            selected.append((candidate, overlap))

    if not selected:
        return None, 0.0, {}

    def order_key(item: tuple[Candidate, float]) -> tuple[float, float, float]:
        candidate = item[0]
        if candidate.element_id in order_map:
            return (0.0, float(order_map[candidate.element_id]), 0.0)

        payload_order = candidate.payload.get("order_index")
        if payload_order is not None:
            try:
                return (1.0, float(payload_order), 0.0)
            except (TypeError, ValueError):
                pass

        if candidate.bbox is not None:
            return (2.0, float(candidate.bbox.y_min), float(candidate.bbox.x_min))
        return (3.0, 0.0, 0.0)

    selected.sort(key=order_key)

    # Only identical text at identical coordinates is a duplicate.
    # Repeated words at different positions remain part of the prediction.
    ordered: list[tuple[Candidate, float]] = []
    seen_blocks: set[tuple[str, float, float, float, float]] = set()
    for candidate, overlap in selected:
        normalized = _pred_text(candidate)
        if not normalized:
            continue
        box = candidate.bbox  # selected candidates always have a bbox
        key = (normalized, box.x_min, box.y_min, box.x_max, box.y_max)
        if key in seen_blocks:
            continue
        seen_blocks.add(key)
        ordered.append((candidate, overlap))

    if not ordered:
        return None, 0.0, {}

    raw_parts: list[str] = []
    for candidate, _ in ordered:
        payload = candidate.payload
        raw = str(
            payload.get("raw_text")
            or payload.get("text")
            or payload.get("normalized_text")
            or ""
        ).strip()
        if raw:
            raw_parts.append(raw)

    if not raw_parts:
        return None, 0.0, {}

    raw_text = "\n".join(raw_parts)
    normalized_text = normalize_text(raw_text)
    source_ids = [candidate.element_id for candidate, _ in ordered]
    source_boxes = [
        candidate.bbox
        for candidate, _ in ordered
        if candidate.bbox is not None
    ]
    overlaps = [overlap for _, overlap in ordered]

    aggregate_bbox = _union_bbox(source_boxes)
    synthetic_id = f"text_aggregate:{gt.object_id}:{len(source_ids)}"

    payload: dict[str, Any] = {
        "element_id": synthetic_id,
        "page_number": gt.page,
        "bbox": aggregate_bbox.model_dump(mode="json") if aggregate_bbox else None,
        "raw_text": raw_text,
        "normalized_text": normalized_text,
        "text": normalized_text,
        "block_type": "synthetic_multiblock",
        "source_element_ids": source_ids,
        "source_block_count": len(source_ids),
    }

    candidate = Candidate(
        element_id=synthetic_id,
        object_type="text",
        page=gt.page,
        bbox=aggregate_bbox,
        payload=payload,
    )

    context = {
        "text_multiblock": True,
        "source_element_ids": source_ids,
        "source_block_count": len(source_ids),
        "selection_metric": "intersection_over_smaller",
        "selection_threshold": cfg.text_multiblock_min_overlap,
        "max_spatial_overlap": max(overlaps),
        "mean_spatial_overlap": sum(overlaps) / len(overlaps),
    }
    return candidate, max(overlaps), context


def _complete_text_regions(
    provisional: list[tuple[GroundTruthObject, int | None, float, str]],
    candidates: list[Candidate],
    page: Page,
    cfg: MatchingConfig,
) -> dict[str, tuple[Candidate, float, dict[str, Any]]]:
    """Complete fragmented Hungarian matches and retain the unmatched fallback.

    Hungarian anchors cannot be reassigned. Free blocks have one spatial owner:
    greatest existing overlap metric, then IoU, then stable GT ID. Reference
    text and evaluation scores never select between single/aggregate text.
    """
    reserved = {col for _, col, _, _ in provisional if col is not None}
    regions = []
    for gt, col, score, method in sorted(provisional, key=lambda row: row[0].object_id):
        if gt.bbox is None:
            continue
        anchor = candidates[col] if col is not None else None
        if anchor is not None:
            # Preserve nonspatial matches and whole-region/page predictions.
            if (anchor.bbox is None or _bbox_contains(anchor.bbox, gt.bbox)
                    or _bbox_intersection_over_smaller(gt.bbox, anchor.bbox)
                    < cfg.text_multiblock_min_overlap):
                continue
        regions.append((gt, anchor, score, method))

    owned: dict[str, list[Candidate]] = {gt.object_id: [] for gt, *_ in regions}
    for idx, candidate in enumerate(candidates):
        if idx in reserved or candidate.bbox is None or not _pred_text(candidate):
            continue
        owners = []
        for gt, anchor, _, _ in regions:
            overlap = _bbox_intersection_over_smaller(gt.bbox, candidate.bbox)
            if overlap < cfg.text_multiblock_min_overlap:
                continue
            if anchor is not None and (
                _bbox_contains(candidate.bbox, gt.bbox)
                or not _extends_text_anchor(gt.bbox, anchor.bbox, candidate.bbox)
            ):
                continue
            owners.append((overlap, bbox_iou(gt.bbox, candidate.bbox), gt.object_id))
        if owners:
            # regions are sorted by ID, so max's first-on-tie behavior is stable.
            owner = max(owners, key=lambda value: value[:2])[2]
            owned[owner].append(candidate)

    result = {}
    for gt, anchor, score, method in regions:
        extras = owned[gt.object_id]
        if not extras:
            continue
        aggregate, agg_score, context = _aggregate_text_candidate(
            gt, page, ([anchor] if anchor is not None else []) + extras, cfg,
        )
        if aggregate is None:
            continue
        if anchor is not None:
            # An empty anchor must not disappear during normalization/dedup.
            if anchor.element_id not in context["source_element_ids"]:
                continue
            context.update(hungarian_element_id=anchor.element_id,
                           hungarian_match_score=score, hungarian_match_method=method)
        context["region_alignment_policy"] = "hungarian_spatial_completion_v1"
        result[gt.object_id] = aggregate, agg_score, context
    return result


def _pair_score(
    gt: GroundTruthObject,
    candidate: Candidate,
    cfg: MatchingConfig,
) -> tuple[float, str, bool]:
    # Visual objects are spatial by design.
    visual = gt.object_type in {"image", "diagram", "chemical_structure"}

    if gt.bbox is not None and candidate.bbox is not None:
        score = bbox_iou(gt.bbox, candidate.bbox)
        return score, "bbox_iou", score >= cfg.spatial_min_iou

    if visual:
        return 0.0, "bbox_required", False

    ref_text = _gt_text(gt)
    pred_text = _pred_text(candidate)
    score = normalized_edit_similarity(ref_text, pred_text)
    return score, "content_similarity", score >= cfg.content_min_similarity


def _hungarian_max(weights: list[list[float]]) -> list[int]:
    """Maximum-weight rectangular assignment, one column per row.

    Returns assigned column index for every row. Requires columns >= rows.
    This is the O(n^3) shortest augmenting-path Hungarian algorithm on the
    equivalent minimization problem.
    """
    if not weights:
        return []
    n = len(weights)
    m = len(weights[0])
    if m < n:
        raise ValueError("Hungarian assignment requires columns >= rows")
    if any(len(row) != m for row in weights):
        raise ValueError("Non-rectangular weight matrix")

    max_w = max(max(row) for row in weights)
    cost = [[max_w - w for w in row] for row in weights]

    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment


def match_document(
    ground_truth: list[GroundTruthObject],
    document: StandardizedDocument,
    *,
    config: MatchingConfig | None = None,
) -> list[MatchRecord]:
    cfg = config or MatchingConfig()
    if any(gt.document_id != document.document_id for gt in ground_truth):
        raise ValueError("match_document received Ground Truth from another document")

    pages = {p.page_number: p for p in document.pages}
    grouped: dict[tuple[int, str], list[GroundTruthObject]] = {}
    for gt in ground_truth:
        grouped.setdefault((gt.page, gt.object_type), []).append(gt)

    output: list[MatchRecord] = []

    for (page_no, object_type), gt_group in sorted(grouped.items()):
        page = pages.get(page_no)
        candidates = page_candidates(page, object_type) if page is not None else []
        if object_type == "text":
            candidates = _deduplicate_text_candidates(candidates)

        # Keep the original one-to-one Hungarian matcher as the primary path.
        # Then complete fragmented text anchors using free spatial blocks, or
        # fall back to spatial aggregation for regions with no single match.

        # Add one private dummy column per GT object. Invalid real pairs get a
        # negative weight, so zero-weight dummies are preferred.
        scores: list[list[float]] = []
        meta: list[list[tuple[float, str, bool]]] = []
        for gt in gt_group:
            row_meta = [_pair_score(gt, c, cfg) for c in candidates]
            row = [score if valid else -1.0 for score, _, valid in row_meta]
            row.extend([0.0] * len(gt_group))
            scores.append(row)
            meta.append(row_meta)

        assignment = _hungarian_max(scores) if gt_group else []
        matched_real: set[int] = set()
        provisional: list[tuple[GroundTruthObject, int | None, float, str]] = []

        for row_idx, gt in enumerate(gt_group):
            col = assignment[row_idx] if assignment else -1
            if 0 <= col < len(candidates):
                score, method, valid = meta[row_idx][col]
                if valid:
                    matched_real.add(col)
                    provisional.append((gt, col, score, method))
                    continue
            provisional.append((gt, None, 0.0, "unmatched"))

        text_fallbacks: dict[str, tuple[Candidate, float, dict[str, Any]]] = {}
        if object_type == "text" and page is not None:
            text_fallbacks = _complete_text_regions(provisional, candidates, page, cfg)

        tp = sum(col is not None or gt.object_id in text_fallbacks for gt, col, _, _ in provisional)
        fn = max(0, len(gt_group) - tp)
        unmatched_candidate_count = max(0, len(candidates) - tp)
        detection_context: dict[str, Any] = {
            "detection_policy": cfg.visual_detection_policy,
            "detection_tp": tp,
            "detection_fn": fn,
            "candidate_count": len(candidates),
            "gt_count": len(gt_group),
            "unmatched_candidate_count": unmatched_candidate_count,
        }
        if cfg.visual_detection_policy == "exhaustive_page_f1_v1":
            # This mode is valid only when every object of this type on the
            # evaluated page has been annotated.
            detection_context["detection_fp"] = unmatched_candidate_count

        for gt, col, score, method in provisional:
            candidate = candidates[col] if col is not None else None
            context = (
                detection_context
                if object_type in {"image", "diagram"}
                else {}
            )

            fallback = text_fallbacks.get(gt.object_id)
            if fallback is not None:
                candidate, score, context = fallback
                method = "text_multiblock_overlap"

            output.append(
                MatchRecord(
                    object_id=gt.object_id,
                    document_id=gt.document_id,
                    page=gt.page,
                    object_type=gt.object_type,
                    prediction_element_id=(
                        candidate.element_id if candidate is not None else None
                    ),
                    matched=candidate is not None,
                    match_method=method,
                    match_score=score,
                    reference=gt.reference,
                    prediction=(
                        candidate.payload if candidate is not None else None
                    ),
                    context=context,
                )
            )

    output.sort(key=lambda x: (x.page, x.object_type, x.object_id))
    return output
