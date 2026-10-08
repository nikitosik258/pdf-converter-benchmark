"""GT-independent recovery of explicit and layout-signalled display math.

Adapters retain their native formula objects.  This module only adds two
auditable fallbacks:

* LaTeX already present between provider delimiters;
* plain positioned text around a right-side numbered-equation marker.

The layout fallback never claims to reconstruct LaTeX.  Its output remains
plain OCR/text so extraction quality is measured from the provider payload.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from pdf_benchmark.models import BBox, Formula, Page, StandardizedDocument, TextBlock
from pdf_benchmark.normalization.latex import normalize_latex


_DELIMITED_MATH = re.compile(
    r"(?P<double>\$\$(?P<double_body>.+?)\$\$)"
    r"|(?P<bracket>\\\[(?P<bracket_body>.+?)\\\])"
    r"|(?P<paren>\\\((?P<paren_body>.+?)\\\))"
    r"|(?P<single>(?<!\$)\$(?P<single_body>[^\n$]+?)\$(?!\$))",
    re.DOTALL,
)
_EQUATION_NUMBER = re.compile(r"^\(\d{1,3}\)[.,]?$", re.ASCII)
_EQUATION_AFFIX = re.compile(r"^\s*[,.;]?\s*(?:\(\d{1,3}\)[.,]?)?\s*$", re.ASCII)
_TAGGED_LATEX = re.compile(r"\\(?:tag|eqno)\s*(?:\{|\()?\s*\d+", re.ASCII)
_MATH_SIGNAL = re.compile(
    r"[=<>≤≥∑∫ΓΔαβτλπ∞∂^_]"
    r"|\\(?:frac|dfrac|sum|int|partial|alpha|beta|gamma|Gamma|Delta|infty)"
)
_LONG_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")
_CYRILLIC_WORD = re.compile(r"[А-Яа-яЁё]{3,}")


@dataclass(frozen=True)
class DelimitedMathCandidate:
    latex: str
    start: int
    end: int
    wrapper: str
    display: bool
    numbered: bool


@dataclass
class _ExplicitSource:
    candidate: DelimitedMathCandidate
    block: TextBlock
    sequence_index: int
    formula_only: bool


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return line_start, len(text) if line_end < 0 else line_end


def delimited_math_candidates(text: str) -> list[DelimitedMathCandidate]:
    """Return provider-supplied display LaTeX without consulting benchmark GT."""
    value = str(text or "")
    output: list[DelimitedMathCandidate] = []
    for match in _DELIMITED_MATH.finditer(value):
        kind = match.lastgroup
        if kind is None:
            continue
        body = match.group(f"{kind}_body").strip()
        if not body:
            continue
        line_start, line_end = _line_bounds(value, match.start(), match.end())
        prefix = value[line_start:match.start()]
        suffix = value[match.end():line_end]
        standalone = bool(_EQUATION_AFFIX.fullmatch(prefix + suffix))
        display = kind in {"double", "bracket"} or standalone
        if not display:
            continue
        numbered = bool(_TAGGED_LATEX.search(body)) or bool(
            re.search(r"\(\d{1,3}\)[.,]?\s*$", suffix, re.ASCII)
        )
        output.append(
            DelimitedMathCandidate(
                latex=body,
                start=match.start(),
                end=match.end(),
                wrapper=kind,
                display=display,
                numbered=numbered,
            )
        )
    return output


def _union_bbox(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return BBox(
        x_min=min(box.x_min for box in boxes),
        y_min=min(box.y_min for box in boxes),
        x_max=max(box.x_max for box in boxes),
        y_max=max(box.y_max for box in boxes),
    )


def _same_bbox(left: BBox | None, right: BBox | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.as_list == right.as_list


def _formula_key(value: str) -> str:
    return normalize_latex(value or "")


def _append_formula(
    page: Page,
    *,
    formula: Formula,
    existing: list[tuple[str, BBox | None]],
) -> bool:
    key = _formula_key(formula.latex or formula.raw_text or "")
    if not key:
        return False
    if any(key == other and _same_bbox(formula.bbox, box) for other, box in existing):
        return False
    page.formulas.append(formula)
    page.reading_order.append(formula.element_id)
    existing.append((key, formula.bbox))
    return True


def _block_formula_only(
    text: str,
    candidates: list[DelimitedMathCandidate],
) -> bool:
    cursor = 0
    parts: list[str] = []
    for candidate in candidates:
        parts.append(text[cursor:candidate.start])
        cursor = candidate.end
    parts.append(text[cursor:])
    remainder = "".join(parts)
    return bool(_EQUATION_AFFIX.fullmatch(remainder))


def _explicit_formula_sources(page: Page) -> list[_ExplicitSource]:
    order_map = {element_id: index for index, element_id in enumerate(page.reading_order)}

    def sort_key(block: TextBlock) -> tuple[int, int, float, float]:
        if block.element_id in order_map:
            return (0, order_map[block.element_id], 0.0, 0.0)
        if block.order_index is not None:
            return (1, int(block.order_index), 0.0, 0.0)
        if block.bbox is not None:
            return (2, 0, block.bbox.y_min, block.bbox.x_min)
        return (3, 0, 0.0, 0.0)

    output: list[_ExplicitSource] = []
    for sequence_index, block in enumerate(sorted(page.text_blocks, key=sort_key)):
        candidates = delimited_math_candidates(block.raw_text)
        formula_only = _block_formula_only(block.raw_text, candidates)
        for candidate in candidates:
            output.append(
                _ExplicitSource(
                    candidate=candidate,
                    block=block,
                    sequence_index=sequence_index,
                    formula_only=formula_only,
                )
            )
    return output


def numbered_delimited_math_groups(
    text: str,
) -> list[list[DelimitedMathCandidate]]:
    """Group consecutive display expressions when the run is numbered."""
    candidates = delimited_math_candidates(text)
    if len(candidates) < 2:
        return []
    runs: list[list[DelimitedMathCandidate]] = []
    current = [candidates[0]]
    for candidate in candidates[1:]:
        gap = text[current[-1].end:candidate.start]
        if _EQUATION_AFFIX.fullmatch(gap):
            current.append(candidate)
        else:
            if len(current) >= 2 and any(item.numbered for item in current):
                runs.append(current)
            current = [candidate]
    if len(current) >= 2 and any(item.numbered for item in current):
        runs.append(current)
    return runs


def _cross_block_numbered_groups(
    page: Page,
    sources: list[_ExplicitSource],
) -> list[list[_ExplicitSource]]:
    by_sequence: dict[int, list[_ExplicitSource]] = {}
    for source in sources:
        if source.formula_only:
            by_sequence.setdefault(source.sequence_index, []).append(source)
    ordered_blocks = sorted({source.sequence_index for source in sources})
    if not ordered_blocks:
        return []

    # Only actual adjacent text blocks may form one display system. A separate
    # parenthetical equation-number block is permitted between formula lines.
    order_map = {element_id: index for index, element_id in enumerate(page.reading_order)}
    sorted_blocks = sorted(
        page.text_blocks,
        key=lambda block: (
            0 if block.element_id in order_map else 1,
            order_map.get(block.element_id, block.order_index or 0),
            block.bbox.y_min if block.bbox else 0.0,
            block.bbox.x_min if block.bbox else 0.0,
        ),
    )
    groups: list[list[_ExplicitSource]] = []
    run: list[_ExplicitSource] = []
    run_numbered = False

    def flush() -> None:
        nonlocal run, run_numbered
        if len(run) >= 2 and run_numbered:
            groups.append(list(run))
        run = []
        run_numbered = False

    for sequence_index, block in enumerate(sorted_blocks):
        block_sources = by_sequence.get(sequence_index)
        if block_sources:
            run.extend(block_sources)
            run_numbered = run_numbered or any(
                source.candidate.numbered for source in block_sources
            )
            continue
        if run and _EQUATION_NUMBER.fullmatch(block.raw_text.strip()):
            run_numbered = True
            continue
        flush()
    flush()
    return groups


def _add_explicit_formulas(page: Page) -> int:
    existing = [
        (_formula_key(item.latex or item.raw_text or ""), item.bbox)
        for item in page.formulas
    ]
    sources = _explicit_formula_sources(page)
    added = 0
    serial = 0
    for source in sources:
        candidate = source.candidate
        formula = Formula(
            element_id=f"shared_math_p{page.page_number}_explicit_{serial:04d}",
            page_number=page.page_number,
            bbox=source.block.bbox,
            raw_text=candidate.latex,
            latex=candidate.latex,
            formula_type="display",
            order_index=source.block.order_index,
            provenance={
                "source": "explicit_delimited_latex_v1",
                "source_element_id": source.block.element_id,
                "source_span": [candidate.start, candidate.end],
                "wrapper": candidate.wrapper,
                "gt_independent": True,
            },
        )
        serial += 1
        added += int(_append_formula(page, formula=formula, existing=existing))

    group_keys: set[tuple[str, ...]] = set()
    grouped: list[tuple[list[str], list[BBox], list[str], int | None]] = []
    internal_blocks: set[str] = set()
    for source in sources:
        if source.block.element_id in internal_blocks:
            continue
        internal_blocks.add(source.block.element_id)
        for group in numbered_delimited_math_groups(source.block.raw_text):
            values = [item.latex for item in group]
            key = tuple(_formula_key(item) for item in values)
            if key not in group_keys:
                group_keys.add(key)
                grouped.append((values, [source.block.bbox] if source.block.bbox else [], [source.block.element_id], source.block.order_index))
    for group in _cross_block_numbered_groups(page, sources):
        values = [source.candidate.latex for source in group]
        key = tuple(_formula_key(item) for item in values)
        if key not in group_keys:
            group_keys.add(key)
            grouped.append((
                values,
                [source.block.bbox for source in group if source.block.bbox],
                [source.block.element_id for source in group],
                min((source.block.order_index for source in group if source.block.order_index is not None), default=None),
            ))

    for values, boxes, source_ids, order_index in grouped:
        latex = r" \quad ".join(values)
        formula = Formula(
            element_id=f"shared_math_p{page.page_number}_group_{serial:04d}",
            page_number=page.page_number,
            bbox=_union_bbox(boxes),
            raw_text=latex,
            latex=latex,
            formula_type="display",
            order_index=order_index,
            provenance={
                "source": "numbered_delimited_display_group_v1",
                "source_element_ids": source_ids,
                "group_size": len(values),
                "gt_independent": True,
            },
        )
        serial += 1
        added += int(_append_formula(page, formula=formula, existing=existing))
    return added


def _row_traits(text: str) -> tuple[bool, bool]:
    value = text.strip()
    long_words = _LONG_WORD.findall(value)
    cyrillic_words = _CYRILLIC_WORD.findall(value)
    mathematical = bool(_MATH_SIGNAL.search(value)) or (
        bool(value)
        and len(value) <= 30
        and len(long_words) <= 2
        and bool(re.search(r"[()0-9+\-*/]", value))
    )
    prose = len(cyrillic_words) >= 3 or (
        len(value) > 50 and len(long_words) >= 6
    )
    return mathematical, prose


def _layout_rows(blocks: list[TextBlock]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    ordered = sorted(
        blocks,
        key=lambda block: (
            (block.bbox.y_min + block.bbox.y_max) / 2,
            block.bbox.x_min,
        ),
    )
    for block in ordered:
        box = block.bbox
        center = (box.y_min + box.y_max) / 2
        height = box.y_max - box.y_min
        target = None
        for row in rows[-4:]:
            if abs(center - float(row["center"])) <= max(
                0.006,
                0.55 * max(height, float(row["height"])),
            ):
                target = row
                break
        if target is None:
            rows.append({"center": center, "height": height, "blocks": [block]})
        else:
            row_blocks = target["blocks"]
            row_blocks.append(block)
            count = len(row_blocks)
            target["center"] = (float(target["center"]) * (count - 1) + center) / count
            target["height"] = max(float(target["height"]), height)
    for row in rows:
        row["blocks"].sort(key=lambda block: block.bbox.x_min)
        row["text"] = " ".join(block.raw_text for block in row["blocks"])
        row["mathematical"], row["prose"] = _row_traits(str(row["text"]))
    return rows


def _add_numbered_layout_formulas(page: Page) -> int:
    blocks = [
        block for block in page.text_blocks
        if block.bbox is not None and block.raw_text.strip()
    ]
    if not blocks:
        return 0
    rows = _layout_rows(blocks)
    anchors = [
        block for block in blocks
        if _EQUATION_NUMBER.fullmatch(block.raw_text.strip())
    ]
    existing = [
        (_formula_key(item.latex or item.raw_text or ""), item.bbox)
        for item in page.formulas
    ]
    added = 0
    for anchor_index, anchor in enumerate(anchors):
        anchor_center = (anchor.bbox.y_min + anchor.bbox.y_max) / 2
        row_views: list[dict[str, object]] = []
        for row in rows:
            left_blocks = [
                block for block in row["blocks"]
                if block is not anchor
                and block.bbox.x_min < anchor.bbox.x_min + 0.02
            ]
            left_text = " ".join(block.raw_text for block in left_blocks)
            mathematical, prose = _row_traits(left_text)
            row_views.append({
                "blocks": left_blocks,
                "text": left_text,
                "mathematical": mathematical,
                "prose": prose,
            })
        anchor_row = min(
            range(len(rows)),
            key=lambda index: abs(float(rows[index]["center"]) - anchor_center),
        )
        initial = {
            index for index, row in enumerate(rows)
            if abs(float(row["center"]) - anchor_center) <= 0.055
            and bool(row_views[index]["mathematical"])
            and not bool(row_views[index]["prose"])
        }
        left_evidence = [
            block
            for index in initial
            for block in row_views[index]["blocks"]
        ]
        # A number in neighbouring prose/another column must not borrow an
        # unrelated formula from the left side of the page.
        near_anchor_evidence = [
            block for block in left_evidence
            if -0.02 <= anchor.bbox.x_min - block.bbox.x_max <= 0.25
        ]
        if not near_anchor_evidence:
            continue

        chosen = set(initial)
        last_center = anchor_center
        for index in range(anchor_row + 1, len(rows)):
            row = rows[index]
            view = row_views[index]
            center = float(row["center"])
            if center - anchor_center > 0.22 or center - last_center > 0.045:
                break
            if bool(view["prose"]):
                if center - anchor_center > 0.055:
                    break
                continue
            if bool(view["mathematical"]) or len(str(view["text"])) <= 18:
                chosen.add(index)
                last_center = center
            elif center - anchor_center > 0.055:
                break

        selected: list[TextBlock] = []
        for index in sorted(chosen):
            for block in row_views[index]["blocks"]:
                _mathematical, prose = _row_traits(block.raw_text)
                if prose:
                    continue
                selected.append(block)
        if not selected:
            continue

        unique: list[TextBlock] = []
        seen: set[tuple[str, float, float, float, float]] = set()
        for block in sorted(
            selected,
            key=lambda item: (
                (item.bbox.y_min + item.bbox.y_max) / 2,
                item.bbox.x_min,
            ),
        ):
            key = (
                block.raw_text,
                block.bbox.x_min,
                block.bbox.y_min,
                block.bbox.x_max,
                block.bbox.y_max,
            )
            if key not in seen:
                seen.add(key)
                unique.append(block)
        raw_text = " ".join(block.raw_text for block in unique).strip()
        if not _MATH_SIGNAL.search(raw_text):
            continue
        formula = Formula(
            element_id=f"shared_math_p{page.page_number}_layout_{anchor_index:04d}",
            page_number=page.page_number,
            bbox=_union_bbox([block.bbox for block in unique] + [anchor.bbox]),
            raw_text=raw_text,
            latex=None,
            plain_text=raw_text,
            formula_type="display",
            order_index=min(
                (block.order_index for block in unique if block.order_index is not None),
                default=anchor.order_index,
            ),
            provenance={
                "source": "numbered_equation_layout_v1",
                "equation_number": anchor.raw_text.strip(),
                "anchor_element_id": anchor.element_id,
                "source_element_ids": [block.element_id for block in unique],
                "gt_independent": True,
                "latex_reconstructed": False,
            },
        )
        added += int(_append_formula(page, formula=formula, existing=existing))
    return added


def enrich_math_formulas(document: StandardizedDocument) -> StandardizedDocument:
    """Add explicit/provider-backed math candidates without benchmark GT."""
    explicit_added = 0
    layout_added = 0
    layout_tools = {"pymupdf", "pdfplumber", "pdfminer", "mindee", "ocr_space"}
    tool_name = document.tool.tool_name
    for page in document.pages:
        explicit_added += _add_explicit_formulas(page)
        if tool_name in layout_tools and not page.formulas:
            layout_added += _add_numbered_layout_formulas(page)

    document.metadata = dict(document.metadata)
    document.metadata["math_enrichment"] = {
        "policy": "explicit_latex_then_numbered_layout_v1",
        "explicit_latex_added": explicit_added,
        "numbered_layout_added": layout_added,
        "gt_independent": True,
        "layout_latex_reconstructed": False,
    }
    if explicit_added or layout_added:
        capabilities = dict(document.metadata.get("capabilities") or {})
        capabilities["formulas"] = "native plus GT-independent math enrichment"
        document.metadata["capabilities"] = capabilities
    return document


__all__ = [
    "DelimitedMathCandidate",
    "delimited_math_candidates",
    "enrich_math_formulas",
    "numbered_delimited_math_groups",
]
