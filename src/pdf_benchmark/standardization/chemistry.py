from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pdf_benchmark.models import BBox, ChemicalObject, Page, StandardizedDocument, TextBlock
from pdf_benchmark.normalization.chemistry import normalize_chemical_formula
from pdf_benchmark.utils.io import relative_to_or_name


DETECTOR_VERSION = "gt_independent_chemistry_v1"

_SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
_OCR_CHEM_LOOKALIKES = "АВЕКМНОРСТХ"
_DIGITS = rf"0-9{_SUBSCRIPT_DIGITS}"
_ATOM = rf"(?:[A-Z][a-z]?|[{_OCR_CHEM_LOOKALIKES}])(?:\s*[{_DIGITS}]+)?"
_GROUP = rf"\(\s*(?:{_ATOM}\s*)+\)(?:\s*[{_DIGITS}]+)?"
_SEGMENT = rf"(?:[{_DIGITS}]+\s*)?(?:{_ATOM}|{_GROUP})(?:\s*(?:{_ATOM}|{_GROUP}))*"
_LINEAR_FORMULA_RE = re.compile(
    rf"(?<![A-Za-zА-Яа-я0-9_])"
    rf"(?P<formula>{_SEGMENT}(?:\s*[·⋅•]\s*{_SEGMENT}|\s*-\s*[{_DIGITS}]+\s*(?:{_ATOM}|{_GROUP})+)*)"
    rf"(?![A-Za-zА-Яа-я0-9_])"
)
_ELEMENT_SYMBOLS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu "
    "Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs "
    "Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl "
    "Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh "
    "Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split()
)
_LOOKALIKE_TO_LATIN = str.maketrans(
    {"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X"}
)
_SUBSCRIPT_TO_ASCII = str.maketrans(
    {char: str(index) for index, char in enumerate(_SUBSCRIPT_DIGITS)}
)
_HTML_SUB_RE = re.compile(r"<\s*sub\s*>(.*?)<\s*/\s*sub\s*>", re.I | re.S)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LATEX_SUB_RE = re.compile(r"_\{?([0-9]+)\}?")
_LATEX_TEXT_COMMAND_RE = re.compile(r"\\(?:mathrm|text|ce|chem)\{([^{}]*)\}")
_IMG_SRC_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)


@dataclass(frozen=True)
class _TextSource:
    text: str
    bbox: BBox | None
    source_ids: tuple[str, ...]
    source_kind: str
    member_spans: tuple[tuple[int, int, BBox], ...] = ()
    exact_bbox: bool = False


@dataclass(frozen=True)
class _LinearCandidate:
    formula: str
    bbox: BBox | None
    source_ids: tuple[str, ...]
    source_kind: str


def _formula_scan_text(value: str) -> str:
    text = html.unescape(value or "")
    text = _HTML_SUB_RE.sub(lambda match: match.group(1), text)
    text = _HTML_TAG_RE.sub(" ", text)
    for _ in range(3):
        updated = _LATEX_TEXT_COMMAND_RE.sub(lambda match: match.group(1), text)
        if updated == text:
            break
        text = updated
    text = _LATEX_SUB_RE.sub(lambda match: match.group(1), text)
    return text.replace("{", "").replace("}", "")


def element_symbols(value: str) -> list[str] | None:
    """Parse a candidate for recognition only; never correct the output text."""
    text = value.translate(_LOOKALIKE_TO_LATIN).translate(_SUBSCRIPT_TO_ASCII)
    symbols: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isdigit() or char.isspace() or char in "()[]{}·⋅•.-+=":
            index += 1
            continue
        if not ("A" <= char <= "Z"):
            return None
        symbol = char
        index += 1
        if index < len(text) and "a" <= text[index] <= "z":
            symbol += text[index]
            index += 1
        if symbol not in _ELEMENT_SYMBOLS:
            return None
        symbols.append(symbol)
    return symbols


def linear_formula_candidates(text: str) -> list[tuple[str, int, int]]:
    """Find explicit molecular-formula spans without using benchmark GT."""
    scan_text = _formula_scan_text(text)
    candidates: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in _LINEAR_FORMULA_RE.finditer(scan_text):
        raw_formula = match.group("formula").strip()
        symbols = element_symbols(raw_formula)
        if symbols is None or len(symbols) < 2 or len(set(symbols)) < 2:
            continue
        if not any(char.isdigit() or char in _SUBSCRIPT_DIGITS for char in raw_formula):
            continue
        mapped_formula = raw_formula.translate(_LOOKALIKE_TO_LATIN).translate(_SUBSCRIPT_TO_ASCII)
        numeric_tokens = [int(value) for value in re.findall(r"\d+", mapped_formula)]
        if any(value == 0 or value > 99 for value in numeric_tokens):
            continue
        # OCR-confusable Cyrillic capitals alone commonly encode electrical
        # labels (for example ``КС1``). Retain an all-Cyrillic candidate only
        # for the common H/O pair; mixed Latin/Cyrillic OCR formulas remain
        # eligible and their raw spelling is preserved.
        if not re.search(r"[A-Za-z]", raw_formula) and not {"H", "O"} <= set(symbols):
            continue
        compact_mapped = re.sub(r"[^A-Za-z0-9]", "", mapped_formula)
        if re.fullmatch(r"[A-Z]\d+[A-Z]{2,}", compact_mapped):
            continue
        if re.fullmatch(r"\d+[A-Z]{2,}", compact_mapped) and not {"H", "O"} <= set(symbols):
            continue
        # Reject variable sequences such as ``U₁₁ B``. A detached atom
        # after an indexed token must itself carry an index; spaced OCR formulas
        # such as ``Fe 3 O 4`` still pass.
        if len(symbols) == 2 and re.search(
            rf"[{_DIGITS}]\s+[A-Z{_OCR_CHEM_LOOKALIKES}](?![a-z]|\s*[{_DIGITS}])",
            raw_formula,
        ):
            continue
        item = (raw_formula, match.start("formula"), match.end("formula"))
        if item not in seen:
            seen.add(item)
            candidates.append(item)
    return candidates


def _union_bbox(boxes: Iterable[BBox]) -> BBox | None:
    values = list(boxes)
    if not values:
        return None
    return BBox(
        x_min=min(box.x_min for box in values),
        y_min=min(box.y_min for box in values),
        x_max=max(box.x_max for box in values),
        y_max=max(box.y_max for box in values),
    )


def _bbox_area(box: BBox | None) -> float:
    if box is None:
        return 0.0
    return max(0.0, box.x_max - box.x_min) * max(0.0, box.y_max - box.y_min)


def _bbox_intersection(a: BBox | None, b: BBox | None) -> float:
    if a is None or b is None:
        return 0.0
    return max(0.0, min(a.x_max, b.x_max) - max(a.x_min, b.x_min)) * max(
        0.0, min(a.y_max, b.y_max) - max(a.y_min, b.y_min)
    )


def _bbox_for_span(source: _TextSource, start: int, end: int) -> BBox | None:
    touched = [
        box for member_start, member_end, box in source.member_spans
        if member_end > start and member_start < end
    ]
    if touched:
        return _union_bbox(touched)
    if source.bbox is None:
        return None
    if source.exact_bbox:
        return source.bbox
    height = source.bbox.y_max - source.bbox.y_min
    if height <= 0.035 and len(source.text) <= 240:
        width = source.bbox.x_max - source.bbox.x_min
        denominator = max(1, len(_formula_scan_text(source.text)))
        return BBox(
            x_min=max(0.0, source.bbox.x_min + width * start / denominator),
            y_min=source.bbox.y_min,
            x_max=min(1.0, source.bbox.x_min + width * end / denominator),
            y_max=source.bbox.y_max,
        )
    return None


def _line_sources(blocks: list[TextBlock]) -> list[_TextSource]:
    fragments = [
        block for block in blocks
        if block.bbox is not None and block.raw_text.strip()
        and len(block.raw_text.strip()) <= 40
        and block.bbox.x_max - block.bbox.x_min <= 0.25
    ]
    rows: list[list[TextBlock]] = []
    for block in sorted(
        fragments,
        key=lambda item: (
            (item.bbox.y_min + item.bbox.y_max) / 2,
            item.bbox.x_min,
            item.element_id,
        ),
    ):
        center = (block.bbox.y_min + block.bbox.y_max) / 2
        height = block.bbox.y_max - block.bbox.y_min
        best_index = None
        best_distance = float("inf")
        # Blocks are sorted by vertical center. Only recently created rows can
        # overlap the current fragment; this keeps word-level OCR linear rather
        # than quadratic on documents with tens of thousands of words.
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            row_center = sum((x.bbox.y_min + x.bbox.y_max) / 2 for x in row) / len(row)
            row_height = max(x.bbox.y_max - x.bbox.y_min for x in row)
            distance = abs(center - row_center)
            if center - row_center > 0.05:
                break
            if distance <= max(height, row_height) * 0.8 and distance < best_distance:
                best_index = index
                best_distance = distance
        if best_index is None:
            rows.append([block])
        else:
            rows[best_index].append(block)

    sources: list[_TextSource] = []
    for row in rows:
        ordered = sorted(row, key=lambda item: (item.bbox.x_min, item.element_id))
        groups: list[list[TextBlock]] = []
        for block in ordered:
            if not groups:
                groups.append([block])
                continue
            previous = groups[-1][-1]
            gap = block.bbox.x_min - previous.bbox.x_max
            overlap = min(block.bbox.x_max, previous.bbox.x_max) - max(
                block.bbox.x_min, previous.bbox.x_min
            )
            if gap > 0.035 or overlap > 0.5 * min(
                block.bbox.x_max - block.bbox.x_min,
                previous.bbox.x_max - previous.bbox.x_min,
            ):
                groups.append([block])
            else:
                groups[-1].append(block)

        for group in groups:
            if len(group) < 2:
                continue
            parts: list[str] = []
            spans: list[tuple[int, int, BBox]] = []
            cursor = 0
            for block in group:
                if parts:
                    parts.append(" ")
                    cursor += 1
                value = block.raw_text.strip()
                start = cursor
                parts.append(value)
                cursor += len(value)
                spans.append((start, cursor, block.bbox))
            sources.append(
                _TextSource(
                    text="".join(parts),
                    bbox=_union_bbox(block.bbox for block in group),
                    source_ids=tuple(block.element_id for block in group),
                    source_kind="spatial_text_line",
                    member_spans=tuple(spans),
                )
            )
    return sources


def _page_text_sources(page: Page) -> list[_TextSource]:
    sources = [
        _TextSource(
            text=block.raw_text,
            bbox=block.bbox,
            source_ids=(block.element_id,),
            source_kind="text_block",
            exact_bbox=len(block.raw_text.strip()) <= 32,
        )
        for block in page.text_blocks
        if block.raw_text.strip()
    ]
    sources.extend(_line_sources(page.text_blocks))
    for formula in page.formulas:
        values = [formula.raw_text, formula.plain_text, formula.latex]
        text = next((value for value in values if value and value.strip()), "")
        if text:
            sources.append(
                _TextSource(
                    text=text,
                    bbox=formula.bbox,
                    source_ids=(formula.element_id,),
                    source_kind="math_formula",
                    exact_bbox=True,
                )
            )
    for table in page.tables:
        for cell in table.cells:
            # Multiline table cells are frequently serialized in reading order
            # rather than chemical order (for example ``C H OH\n2``). Native
            # text blocks or single-line cells remain eligible evidence.
            if cell.text.strip() and "\n" not in cell.text and "\r" not in cell.text:
                sources.append(
                    _TextSource(
                        text=cell.text,
                        bbox=cell.bbox,
                        source_ids=(f"{table.element_id}:r{cell.row_index}c{cell.column_index}",),
                        source_kind="table_cell",
                        exact_bbox=len(cell.text.strip()) <= 32,
                    )
                )
    return sources


def _linear_candidates(page: Page) -> list[_LinearCandidate]:
    candidates: list[_LinearCandidate] = []
    for source in _page_text_sources(page):
        for formula, start, end in linear_formula_candidates(source.text):
            candidates.append(
                _LinearCandidate(
                    formula=formula,
                    bbox=_bbox_for_span(source, start, end),
                    source_ids=source.source_ids,
                    source_kind=source.source_kind,
                )
            )

    # Prefer the longest spatial candidate when fragmented words also produced
    # shorter contained formulas. Preserve repetitions at different locations.
    ordered = sorted(
        candidates,
        key=lambda item: (-len(normalize_chemical_formula(item.formula)), item.source_ids),
    )
    kept: list[_LinearCandidate] = []
    for candidate in ordered:
        normalized = normalize_chemical_formula(candidate.formula)
        duplicate = False
        for existing in kept:
            existing_normalized = normalize_chemical_formula(existing.formula)
            overlap = _bbox_intersection(candidate.bbox, existing.bbox)
            contained = overlap >= 0.8 * min(
                _bbox_area(candidate.bbox), _bbox_area(existing.bbox)
            ) if candidate.bbox is not None and existing.bbox is not None else False
            if candidate.source_ids == existing.source_ids and normalized == existing_normalized:
                duplicate = True
                break
            if contained and normalized in existing_normalized:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return sorted(
        kept,
        key=lambda item: (
            item.bbox.y_min if item.bbox else 2.0,
            item.bbox.x_min if item.bbox else 2.0,
            item.source_ids,
            item.formula,
        ),
    )


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _is_structure_header(value: str) -> bool:
    text = _fold(value)
    return (
        ("структурн" in text and "формул" in text)
        or ("structural" in text and "formula" in text)
        or ("chemical" in text and "structure" in text)
    )


def _looks_numeric(value: str) -> bool:
    return bool(re.fullmatch(r"[\s0-9.,+\-]+", value or ""))


def _looks_like_structure_label(value: str) -> bool:
    text = re.sub(r"[^A-Za-zА-Яа-я0-9+=.()\-]", "", value.strip())
    if not text or len(text) > 16:
        return False
    mapped = text.translate(_LOOKALIKE_TO_LATIN)
    if mapped in {"N", "O", "S", "+", "-", "N."}:
        return True
    if "=" in mapped and any(symbol in mapped for symbol in ("N", "O", "S")):
        return True
    symbols = element_symbols(mapped)
    return bool(symbols and any(symbol not in {"C", "H"} for symbol in symbols))


def _resolve_and_copy_asset(
    source: str,
    *,
    raw_dir: Path | None,
    assets_dir: Path | None,
    stem: str,
) -> str | None:
    if not source or source.startswith(("http://", "https://", "data:")):
        return source or None
    if raw_dir is None or assets_dir is None:
        return None
    relative = Path(source.replace("\\", "/"))
    candidates = [raw_dir / relative, raw_dir / "saved" / relative]
    candidates.extend(raw_dir.rglob(relative.name))
    real = next((path for path in candidates if path.is_file()), None)
    if real is None:
        return None
    assets_dir.mkdir(parents=True, exist_ok=True)
    target = assets_dir / f"{stem}{real.suffix or '.png'}"
    shutil.copy2(real, target)
    return relative_to_or_name(target, assets_dir.parent)


def _table_structure_objects(
    page: Page,
    *,
    raw_dir: Path | None,
    assets_dir: Path | None,
) -> list[ChemicalObject]:
    output: list[ChemicalObject] = []
    for table in page.tables:
        header_cells = [cell for cell in table.cells if cell.row_index == 0]
        header_text = " ".join(cell.text for cell in header_cells)
        if not (_is_structure_header(header_text) or any(
            _fold(cell.text).startswith("structure:") for cell in table.cells
        )):
            continue

        structure_col = next(
            (cell.column_index for cell in header_cells if _is_structure_header(cell.text)),
            None,
        )
        if structure_col is None:
            structure_tokens = [
                cell.column_index for cell in header_cells
                if "структурн" in _fold(cell.text) or "structur" in _fold(cell.text)
            ]
            formula_tokens = [
                cell.column_index for cell in header_cells
                if "формул" in _fold(cell.text) or "formula" in _fold(cell.text)
            ]
            if structure_tokens and formula_tokens and structure_tokens[0] == formula_tokens[0]:
                structure_col = structure_tokens[0]
            elif table.columns >= 4:
                structure_col = 1

        name_col = min((cell.column_index for cell in header_cells), default=0)
        row_starts = sorted({
            cell.row_index
            for cell in table.cells
            if cell.row_index > 0 and cell.column_index == name_col and cell.text.strip()
        })
        if not row_starts:
            row_starts = sorted({
                cell.row_index
                for cell in table.cells
                if cell.row_index > 0 and _fold(cell.text).startswith("structure:")
            })
        if not row_starts:
            continue

        image_sources = _IMG_SRC_RE.findall(table.html or "")
        table_box = table.bbox
        logical_columns = max(table.columns, 4 if structure_col is None else table.columns)
        for item_index, row_start in enumerate(row_starts):
            row_end = row_starts[item_index + 1] if item_index + 1 < len(row_starts) else table.rows
            name_cell = next(
                (cell for cell in table.cells if cell.row_index == row_start and cell.column_index == name_col),
                None,
            )
            structure_cell = next(
                (
                    cell for cell in table.cells
                    if cell.row_index == row_start
                    and structure_col is not None
                    and cell.column_index == structure_col
                ),
                None,
            )

            inferred = None
            if table_box is not None and table.rows > 0:
                y_min = table_box.y_min + (table_box.y_max - table_box.y_min) * row_start / table.rows
                y_max = table_box.y_min + (table_box.y_max - table_box.y_min) * row_end / table.rows
                if structure_col is not None:
                    x_min = table_box.x_min + (table_box.x_max - table_box.x_min) * structure_col / logical_columns
                    x_max = table_box.x_min + (table_box.x_max - table_box.x_min) * (structure_col + 1) / logical_columns
                else:
                    left_edges = [
                        cell.bbox.x_max for cell in table.cells
                        if cell.row_index in row_starts and cell.column_index == name_col and cell.bbox is not None
                    ]
                    numeric_edges = [
                        cell.bbox.x_min for cell in table.cells
                        if cell.row_index > 0 and _looks_numeric(cell.text) and cell.bbox is not None
                    ]
                    if not left_edges or not numeric_edges:
                        continue
                    x_min, x_max = max(left_edges), min(numeric_edges)
                if x_max > x_min and y_max > y_min:
                    inferred = BBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)

            image = None
            if inferred is not None:
                candidates = [
                    value for value in page.images
                    if value.bbox is not None
                    and _bbox_intersection(value.bbox, inferred) > 0
                ]
                if candidates:
                    image = max(candidates, key=lambda value: _bbox_intersection(value.bbox, inferred))

            bbox = image.bbox if image is not None else None
            if bbox is None and structure_cell is not None and structure_cell.bbox is not None:
                if not structure_cell.text.strip() or not _looks_numeric(structure_cell.text):
                    bbox = structure_cell.bbox
            bbox = bbox or inferred
            if bbox is None:
                continue

            labels: list[str] = []
            if structure_cell is not None and structure_cell.text.strip() and not _looks_numeric(structure_cell.text):
                value = re.sub(r"^structure\s*:\s*", "", structure_cell.text.strip(), flags=re.I)
                if value:
                    labels.append(value)
            labels.extend(
                block.raw_text.strip()
                for block in page.text_blocks
                if block.bbox is not None
                and _bbox_intersection(block.bbox, bbox) > 0
                and _looks_like_structure_label(block.raw_text)
            )

            asset = image.asset_path if image is not None else None
            if not asset and item_index < len(image_sources):
                asset = _resolve_and_copy_asset(
                    image_sources[item_index],
                    raw_dir=raw_dir,
                    assets_dir=assets_dir,
                    stem=f"p{page.page_number}_chem_structure_{len(output):04d}",
                )
            output.append(
                ChemicalObject(
                    element_id=f"chem_detect_p{page.page_number}_structure_{len(output):04d}",
                    page_number=page.page_number,
                    bbox=bbox,
                    subtype="structure",
                    text_labels=list(dict.fromkeys(labels)),
                    asset_path=asset,
                    order_index=None,
                    provenance={
                        "source": "semantic_structural_formula_table",
                        "detector_version": DETECTOR_VERSION,
                        "source_table_id": table.element_id,
                        "source_name": name_cell.text.strip() if name_cell else "",
                        "source_structure_cell": structure_cell.text.strip() if structure_cell else "",
                        "bbox_policy": "native_image_or_semantic_table_grid_v1",
                        "gt_used": False,
                    },
                )
            )
    return output


def _header_line(page: Page) -> tuple[BBox, list[TextBlock]] | None:
    # Prefer a column header formed from adjacent ``structural`` and ``formula``
    # tokens when it has a name column on the left and another header on the
    # right. This distinguishes a table header from a nearby section title such
    # as "Structural formulas of dyes" without using document-specific text or
    # Ground Truth coordinates.
    structural_tokens = [
        block for block in page.text_blocks
        if block.bbox is not None
        and any(stem in _fold(block.raw_text) for stem in ("структурн", "structur"))
    ]
    formula_tokens = [
        block for block in page.text_blocks
        if block.bbox is not None
        and any(stem in _fold(block.raw_text) for stem in ("формул", "formula"))
    ]
    candidates: list[tuple[int, float, BBox, list[TextBlock]]] = []
    for structural in structural_tokens:
        structural_center = (structural.bbox.y_min + structural.bbox.y_max) / 2
        for formula in formula_tokens:
            formula_center = (formula.bbox.y_min + formula.bbox.y_max) / 2
            if abs(structural_center - formula_center) > 0.02:
                continue
            if formula.bbox.x_min < structural.bbox.x_min or formula.bbox.x_min - structural.bbox.x_max > 0.08:
                continue
            box = _union_bbox((structural.bbox, formula.bbox))
            if box is None:
                continue
            same_row = [
                block for block in page.text_blocks
                if block.bbox is not None
                and abs((block.bbox.y_min + block.bbox.y_max) / 2 - (box.y_min + box.y_max) / 2) <= 0.02
            ]
            has_name_left = any(
                block.bbox.x_max <= box.x_min
                and _fold(block.raw_text) in {"название", "name", "compound"}
                for block in same_row
            )
            has_right_header = any(block.bbox.x_min > box.x_max for block in same_row)
            score = int(has_name_left) + int(has_right_header)
            candidates.append((score, box.y_min, box, [structural, formula]))
    if candidates:
        score, _, box, members = max(candidates, key=lambda item: (item[0], item[1]))
        if score >= 2:
            return box, members

    sources = _line_sources(page.text_blocks)
    source = next((value for value in sources if _is_structure_header(value.text)), None)
    if source is None:
        source = next(
            (
                _TextSource(
                    text=block.raw_text,
                    bbox=block.bbox,
                    source_ids=(block.element_id,),
                    source_kind="text_block",
                )
                for block in page.text_blocks
                if block.bbox is not None and _is_structure_header(block.raw_text)
            ),
            None,
        )
    if source is None or source.bbox is None:
        return None
    members = [block for block in page.text_blocks if block.element_id in source.source_ids]
    return source.bbox, members


def _page_structure_objects(page: Page) -> list[ChemicalObject]:
    header = _header_line(page)
    if header is None:
        return []
    header_box, _ = header
    output: list[ChemicalObject] = []

    images = [
        image for image in page.images
        if image.bbox is not None
        and image.bbox.y_min >= header_box.y_max
        and header_box.x_min - 0.18 <= (image.bbox.x_min + image.bbox.x_max) / 2 <= header_box.x_max + 0.18
        and image.bbox.y_min <= header_box.y_max + 0.55
    ]
    for image in sorted(images, key=lambda value: (value.bbox.y_min, value.bbox.x_min)):
        labels = [
            block.raw_text.strip()
            for block in page.text_blocks
            if block.bbox is not None
            and _bbox_intersection(block.bbox, image.bbox) > 0
            and _looks_like_structure_label(block.raw_text)
        ]
        output.append(
            ChemicalObject(
                element_id=f"chem_detect_p{page.page_number}_structure_{len(output):04d}",
                page_number=page.page_number,
                bbox=image.bbox,
                subtype="structure",
                text_labels=list(dict.fromkeys(labels)),
                asset_path=image.asset_path,
                provenance={
                    "source": "image_under_structural_formula_header",
                    "detector_version": DETECTOR_VERSION,
                    "source_image_id": image.element_id,
                    "bbox_policy": "native_image_bbox_v1",
                    "gt_used": False,
                },
            )
        )
    if output:
        return output

    # OCR-only providers can expose the atom/group labels without an image or
    # table object. Reconstruct row regions from the explicit table header,
    # left-column record names, and labels inside the structure column.
    same_header_row = [
        block for block in page.text_blocks
        if block.bbox is not None
        and abs((block.bbox.y_min + block.bbox.y_max) / 2 - (header_box.y_min + header_box.y_max) / 2) <= 0.02
    ]
    left_header = next(
        (
            block for block in same_header_row
            if _fold(block.raw_text) in {"название", "name", "compound"}
        ),
        None,
    )
    right_headers = [block for block in same_header_row if block.bbox.x_min > header_box.x_max]
    if left_header is None or not right_headers:
        return []
    left_boundary = (left_header.bbox.x_max + header_box.x_min) / 2
    right_boundary = (header_box.x_max + min(block.bbox.x_min for block in right_headers)) / 2
    labels = [
        block for block in page.text_blocks
        if block.bbox is not None
        and block.bbox.y_min > header_box.y_max
        and block.bbox.y_min < header_box.y_max + 0.55
        and left_boundary <= (block.bbox.x_min + block.bbox.x_max) / 2 <= right_boundary
        and _looks_like_structure_label(block.raw_text)
    ]
    if not labels:
        return []
    label_max_y = max(block.bbox.y_max for block in labels)
    name_blocks = [
        block for block in page.text_blocks
        if block.bbox is not None
        and block.bbox.y_min > header_box.y_max
        and block.bbox.y_min <= label_max_y
        and (block.bbox.x_min + block.bbox.x_max) / 2 < left_boundary
        and block.raw_text.strip()
    ]
    name_groups: list[list[TextBlock]] = []
    for block in sorted(name_blocks, key=lambda value: (value.bbox.y_min, value.bbox.x_min)):
        if not name_groups or block.bbox.y_min - max(x.bbox.y_max for x in name_groups[-1]) > 0.022:
            name_groups.append([block])
        else:
            name_groups[-1].append(block)
    name_groups = [group for group in name_groups if any(len(block.raw_text.strip()) >= 3 for block in group)]
    for index, group in enumerate(name_groups):
        group_box = _union_bbox(block.bbox for block in group)
        if group_box is None:
            continue
        y_min = header_box.y_max if index == 0 else (
            max(block.bbox.y_max for block in name_groups[index - 1]) + group_box.y_min
        ) / 2
        y_max = label_max_y + 0.012 if index + 1 == len(name_groups) else (
            group_box.y_max + min(block.bbox.y_min for block in name_groups[index + 1])
        ) / 2
        region_labels = [
            block.raw_text.strip() for block in labels
            if y_min <= (block.bbox.y_min + block.bbox.y_max) / 2 < y_max
        ]
        if len(region_labels) < 2:
            continue
        bbox = BBox(
            x_min=max(0.0, left_boundary),
            y_min=max(0.0, y_min),
            x_max=min(1.0, right_boundary),
            y_max=min(1.0, y_max),
        )
        output.append(
            ChemicalObject(
                element_id=f"chem_detect_p{page.page_number}_structure_{len(output):04d}",
                page_number=page.page_number,
                bbox=bbox,
                subtype="structure",
                text_labels=list(dict.fromkeys(region_labels)),
                provenance={
                    "source": "spatial_structure_labels_under_explicit_header",
                    "detector_version": DETECTOR_VERSION,
                    "source_name_ids": [block.element_id for block in group],
                    "bbox_policy": "semantic_header_and_row_regions_v1",
                    "gt_used": False,
                },
            )
        )
    return output


def enrich_chemical_objects(
    document: StandardizedDocument,
    *,
    raw_dir: Path | None = None,
    assets_dir: Path | None = None,
) -> StandardizedDocument:
    """Add chemistry objects from explicit provider evidence, without GT.

    Existing adapter-native chemical objects are authoritative. Missing linear
    objects use one strict formula recognizer across all tools. Missing
    structures use explicit structural-formula table semantics, native images,
    or spatially grouped atom labels below an explicit structure header.
    """
    totals = {"linear_added": 0, "structures_added": 0}
    for page in document.pages:
        if not any(item.subtype == "linear_formula" for item in page.chemical_objects):
            for index, candidate in enumerate(_linear_candidates(page)):
                page.chemical_objects.append(
                    ChemicalObject(
                        element_id=f"chem_detect_p{page.page_number}_linear_{index:04d}",
                        page_number=page.page_number,
                        bbox=candidate.bbox,
                        subtype="linear_formula",
                        raw_formula=candidate.formula,
                        provenance={
                            "source": candidate.source_kind,
                            "source_element_ids": list(candidate.source_ids),
                            "recognition_policy": "strict_element_syntax_v2",
                            "detector_version": DETECTOR_VERSION,
                            "chemical_correction_applied": False,
                            "gt_used": False,
                        },
                    )
                )
                totals["linear_added"] += 1

        if not any(item.subtype == "structure" for item in page.chemical_objects):
            structures = _table_structure_objects(
                page,
                raw_dir=raw_dir,
                assets_dir=assets_dir,
            )
            if not structures:
                structures = _page_structure_objects(page)
            page.chemical_objects.extend(structures)
            totals["structures_added"] += len(structures)

        existing_order = [value for value in page.reading_order if value]
        known = set(existing_order)
        for item in page.chemical_objects:
            if item.element_id not in known:
                page.reading_order.append(item.element_id)
                known.add(item.element_id)
            if item.order_index is None:
                item.order_index = page.reading_order.index(item.element_id)

    document.metadata["chemistry_enrichment"] = {
        "detector_version": DETECTOR_VERSION,
        "gt_used": False,
        **totals,
    }
    capabilities = document.metadata.get("capabilities")
    if isinstance(capabilities, dict):
        capabilities["chemical_objects"] = True
    return document
