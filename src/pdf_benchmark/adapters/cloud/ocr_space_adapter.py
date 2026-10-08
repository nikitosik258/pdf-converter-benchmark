from __future__ import annotations

import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import unquote

from pdf_benchmark.adapters.base import ToolExecutionError, ToolOutputParseError
from pdf_benchmark.adapters.cloud.base import BaseCloudAdapter
from pdf_benchmark.adapters.cloud.helpers import pdf_page_count, pdf_page_sizes, split_pdf
from pdf_benchmark.models import (
    BBox,
    ChemicalObject,
    Formula,
    Page,
    RawToolResult,
    StandardizedDocument,
    Table,
    TableCell,
    TextBlock,
)
from pdf_benchmark.standardization.chemistry import linear_formula_candidates
from pdf_benchmark.standardization.math import (
    delimited_math_candidates,
    numbered_delimited_math_groups,
)
from pdf_benchmark.utils.io import ensure_dir, read_json, write_json


_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_PARAGRAPH_SEPARATOR = re.compile(r"\n[ \t]*\n+")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[(?P<alt>[^]]*)\]\((?P<url>https?://[^)\s]+)\)")
_SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
_OCR_CHEM_LOOKALIKES = "АВЕКМНОРСТХ"
_CHEM_ATOM_PATTERN = rf"(?:[A-Z][a-z]?|[{_OCR_CHEM_LOOKALIKES}])[0-9{_SUBSCRIPT_DIGITS}]*"
_CHEM_GROUP_PATTERN = rf"\((?:{_CHEM_ATOM_PATTERN})+\)[0-9{_SUBSCRIPT_DIGITS}]*"
_LINEAR_FORMULA_RE = re.compile(
    rf"(?<![A-Za-zА-Яа-я0-9_])"
    rf"(?P<formula>{_CHEM_ATOM_PATTERN}"
    rf"(?:{_CHEM_ATOM_PATTERN}|\s*{_CHEM_GROUP_PATTERN}"
    rf"|\s*[·⋅•.\-]\s*[0-9{_SUBSCRIPT_DIGITS}]*\s*"
    rf"(?:{_CHEM_ATOM_PATTERN}|{_CHEM_GROUP_PATTERN}))+"
    r")"
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
_SUBSCRIPT_TO_ASCII = str.maketrans({char: str(index) for index, char in enumerate(_SUBSCRIPT_DIGITS)})


@dataclass(frozen=True)
class _MarkdownChemicalStructure:
    line_index: int
    row_label: str
    alt_text: str
    source_url: str
    expression: str
    text_labels: tuple[str, ...]


def _bounded_text_chunks(text: str, max_chars: int) -> list[str]:
    """Split one logical paragraph without discarding provider text."""
    remaining = text.strip()
    chunks: list[str] = []
    while len(remaining) > max_chars:
        candidates = (
            remaining.rfind("\n", 0, max_chars + 1),
            remaining.rfind(" ", 0, max_chars + 1),
            remaining.rfind("|", 0, max_chars + 1),
        )
        boundary = max(candidates)
        if boundary < max_chars // 2:
            boundary = max_chars
        chunk = remaining[:boundary].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[boundary:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _parsed_text_segments(text: str, max_chars: int) -> list[str]:
    """Create GT-independent paragraphs, with bounded chunks for malformed output."""
    normalized_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    segments: list[str] = []
    for paragraph in _PARAGRAPH_SEPARATOR.split(normalized_newlines):
        if paragraph.strip():
            segments.extend(_bounded_text_chunks(paragraph, max_chars))
    return segments


def _element_symbols(value: str) -> list[str] | None:
    """Parse a formula fragment for recognition only, without changing output."""
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


def _linear_formula_candidates(text: str) -> list[tuple[str, int, int]]:
    """Compatibility wrapper for the shared GT-independent recognizer."""
    # CodeCogs expressions are structure evidence. Remove complete Markdown
    # image constructs before applying the shared linear-formula detector.
    scan_text = _MARKDOWN_IMAGE_RE.sub(lambda match: " " * len(match.group(0)), text)
    return linear_formula_candidates(scan_text)


def _latex_command_argument(text: str, command: str) -> str | None:
    marker = f"\\{command}{{"
    start = text.find(marker)
    if start < 0:
        return None
    content_start = start + len(marker)
    depth = 1
    for index in range(content_start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[content_start:index]
    return None


def _structure_text_labels(expression: str) -> tuple[str, ...]:
    """Extract visible heteroatom/group labels from an explicit structure string."""
    labels: list[str] = []
    masked = list(expression)
    for match in re.finditer(r"([A-Z][a-z]?)\s*=\s*([A-Z][a-z]?)", expression):
        left, right = match.group(1), match.group(2)
        if left in _ELEMENT_SYMBOLS and right in _ELEMENT_SYMBOLS:
            labels.append(f"{left}={right}")
            for index in range(match.start(), match.end()):
                masked[index] = " "

    remaining = "".join(masked)
    for match in re.finditer(r"[A-Z][A-Za-z0-9]*(?:[+-](?=-|[\)\]]|$))?", remaining):
        fragment = match.group(0)
        symbols = _element_symbols(fragment)
        if not symbols or set(symbols) <= {"C", "H"}:
            continue
        if len(symbols) == 1 and fragment[-1:] not in {"+", "-"}:
            continue

        multiplier = 1
        if match.start() > 0 and remaining[match.start() - 1] == "(":
            suffix = remaining[match.end():]
            multiplier_match = re.match(r"\)([0-9]+)", suffix)
            if multiplier_match:
                multiplier = max(1, int(multiplier_match.group(1)))
        labels.extend([fragment] * multiplier)
    return tuple(labels)


def _markdown_chemical_structures(text: str) -> list[_MarkdownChemicalStructure]:
    structures: list[_MarkdownChemicalStructure] = []
    for line_index, line in enumerate(text.splitlines()):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        for match in _MARKDOWN_IMAGE_RE.finditer(line):
            decoded_url = unquote(match.group("url"))
            expression = _latex_command_argument(decoded_url, "chem")
            if not expression:
                continue

            row_label = ""
            for cell_index, cell in enumerate(cells):
                if match.group("url") in cell and cell_index > 0:
                    row_label = next(
                        (candidate for candidate in reversed(cells[:cell_index]) if candidate),
                        "",
                    )
                    break
            structures.append(
                _MarkdownChemicalStructure(
                    line_index=line_index,
                    row_label=row_label,
                    alt_text=match.group("alt").strip(),
                    source_url=match.group("url"),
                    expression=expression.strip(),
                    text_labels=_structure_text_labels(expression),
                )
            )
    return structures


def _match_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)
        if len(token) >= 2
    }


def _label_key(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold()).replace("−", "-")


def _structure_overlay_bboxes(
    structures: list[_MarkdownChemicalStructure],
    overlay_lines: list[dict[str, Any]],
    *,
    page_width: float,
    page_height: float,
    pixels_per_point: float,
) -> tuple[list[BBox | None], list[dict[str, Any]]]:
    """Infer table-cell boxes from provider overlay rows and formula labels."""
    entries: list[dict[str, Any]] = []
    for line_index, line in enumerate(overlay_lines):
        text = _overlay_line_text(line)
        bbox, _ = _overlay_line_bbox(
            line,
            page_width=page_width,
            page_height=page_height,
            pixels_per_point=pixels_per_point,
        )
        if text and bbox is not None:
            entries.append({"line_index": line_index, "text": text, "bbox": bbox})

    anchors: list[float | None] = []
    name_line_indices: list[list[int]] = []
    for structure in structures:
        row_tokens = _match_tokens(structure.row_label)
        matches: list[dict[str, Any]] = []
        for entry in entries:
            line_tokens = _match_tokens(entry["text"])
            overlap = row_tokens & line_tokens
            denominator = sum(len(token) for token in line_tokens)
            coverage = sum(len(token) for token in overlap) / denominator if denominator else 0.0
            if overlap and max(map(len, overlap)) >= 3 and coverage >= 0.5:
                matches.append(entry)
        name_line_indices.append([entry["line_index"] for entry in matches])
        anchors.append(
            median((entry["bbox"].y_min + entry["bbox"].y_max) / 2 for entry in matches)
            if matches
            else None
        )

    valid_anchors = [value for value in anchors if value is not None]
    if len(structures) < 2 or len(valid_anchors) != len(structures):
        return [None] * len(structures), [
            {"name_overlay_line_indices": indices, "formula_overlay_line_indices": []}
            for indices in name_line_indices
        ]
    numeric_anchors = [float(value) for value in anchors if value is not None]
    if any(right <= left for left, right in zip(numeric_anchors, numeric_anchors[1:])):
        return [None] * len(structures), [
            {"name_overlay_line_indices": indices, "formula_overlay_line_indices": []}
            for indices in name_line_indices
        ]

    boundaries = [
        max(0.0, numeric_anchors[0] - (numeric_anchors[1] - numeric_anchors[0]) / 2)
    ]
    boundaries.extend(
        (left + right) / 2 for left, right in zip(numeric_anchors, numeric_anchors[1:])
    )
    boundaries.append(
        min(1.0, numeric_anchors[-1] + (numeric_anchors[-1] - numeric_anchors[-2]) / 2)
    )

    label_keys = [_label_key(label) for structure in structures for label in structure.text_labels]
    formula_entries: list[dict[str, Any]] = []
    for entry in entries:
        center_y = (entry["bbox"].y_min + entry["bbox"].y_max) / 2
        if not boundaries[0] <= center_y <= boundaries[-1]:
            continue
        entry_key = _label_key(entry["text"])
        if not entry_key or len(entry_key) > 16:
            continue
        if any(SequenceMatcher(None, entry_key, label).ratio() >= 0.72 for label in label_keys):
            formula_entries.append(entry)

    if not formula_entries:
        return [None] * len(structures), [
            {"name_overlay_line_indices": indices, "formula_overlay_line_indices": []}
            for indices in name_line_indices
        ]

    widths = [entry["bbox"].x_max - entry["bbox"].x_min for entry in formula_entries]
    padding = max(0.01, median(widths) * 0.75)
    x_min = max(0.0, min(entry["bbox"].x_min for entry in formula_entries) - padding)
    x_max = min(1.0, max(entry["bbox"].x_max for entry in formula_entries) + padding)

    bboxes: list[BBox | None] = []
    associations: list[dict[str, Any]] = []
    for index, name_indices in enumerate(name_line_indices):
        y_min, y_max = boundaries[index], boundaries[index + 1]
        row_formula_indices = [
            entry["line_index"]
            for entry in formula_entries
            if y_min <= (entry["bbox"].y_min + entry["bbox"].y_max) / 2 <= y_max
        ]
        bboxes.append(BBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max))
        associations.append(
            {
                "name_overlay_line_indices": name_indices,
                "formula_overlay_line_indices": row_formula_indices,
            }
        )
    return bboxes, associations


def _overlay_line_text(line: dict[str, Any]) -> str:
    explicit = str(line.get("LineText") or "").strip()
    if explicit:
        return explicit
    return " ".join(
        str(word.get("WordText") or "").strip()
        for word in (line.get("Words") or [])
        if str(word.get("WordText") or "").strip()
    )


def _overlay_line_bbox(
    line: dict[str, Any],
    *,
    page_width: float,
    page_height: float,
    pixels_per_point: float,
) -> tuple[BBox | None, dict[str, float] | None]:
    words = line.get("Words") or []
    pixel_boxes: list[tuple[float, float, float, float]] = []
    for word in words:
        try:
            left = float(word.get("Left"))
            top = float(word.get("Top"))
            width = float(word.get("Width"))
            height = float(word.get("Height"))
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        pixel_boxes.append((left, top, left + width, top + height))

    if not pixel_boxes or page_width <= 0 or page_height <= 0 or pixels_per_point <= 0:
        return None, None

    raw = {
        "x_min": min(box[0] for box in pixel_boxes),
        "y_min": min(box[1] for box in pixel_boxes),
        "x_max": max(box[2] for box in pixel_boxes),
        "y_max": max(box[3] for box in pixel_boxes),
    }
    canvas_width = page_width * pixels_per_point
    canvas_height = page_height * pixels_per_point

    def clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    normalized = BBox(
        x_min=clamp(raw["x_min"] / canvas_width),
        y_min=clamp(raw["y_min"] / canvas_height),
        x_max=clamp(raw["x_max"] / canvas_width),
        y_max=clamp(raw["y_max"] / canvas_height),
    )
    if normalized.x_min >= normalized.x_max or normalized.y_min >= normalized.y_max:
        return None, raw
    return normalized, raw


def _markdown_tables(text: str) -> list[list[list[str]]]:
    lines = text.splitlines()
    out: list[list[list[str]]] = []
    i = 0
    while i + 1 < len(lines):
        if "|" not in lines[i] or not _TABLE_SEPARATOR.match(lines[i + 1]):
            i += 1
            continue

        rows: list[list[str]] = []
        header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        rows.append(header)
        i += 2
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            rows.append(row)
            i += 1
        out.append(rows)
    return out


class OCRSpaceAdapter(BaseCloudAdapter):
    tool_name = "ocr_space"
    distribution_name = "requests"
    pinned_version = "2.32.5"
    required_env = ("OCR_SPACE_API_KEY",)

    def model_versions(self) -> dict[str, str]:
        return {
            "ocr_engine": str(self.config.get("ocr_engine", 3)),
            "endpoint": str(self.config.get("endpoint", "https://api.ocr.space/parse/image")),
        }

    def _post_chunk(self, chunk: Path) -> tuple[dict[str, Any], float]:
        import os
        import requests

        endpoint = str(self.config.get("endpoint", "https://api.ocr.space/parse/image"))
        timeout = float(self.config.get("request_timeout_seconds", 180))
        headers = {"apikey": os.environ["OCR_SPACE_API_KEY"]}
        data = {
            "language": str(self.config.get("language", "auto")),
            "isOverlayRequired": "true" if bool(self.config.get("overlay", True)) else "false",
            "isTable": "true" if bool(self.config.get("is_table", True)) else "false",
            "OCREngine": str(self.config.get("ocr_engine", 3)),
            "detectOrientation": "true",
            "scale": "false",
        }

        def call():
            with chunk.open("rb") as fh:
                response = requests.post(
                    endpoint,
                    headers=headers,
                    data=data,
                    files={"file": (chunk.name, fh, "application/pdf")},
                    timeout=timeout,
                )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                response.raise_for_status()
            if response.status_code >= 400:
                raise ToolExecutionError(
                    f"OCR.Space HTTP {response.status_code}: {response.text[:2000]}"
                )
            return response

        started = time.monotonic()
        response = self.retry_call(
            call,
            is_retryable=lambda exc: (
                getattr(getattr(exc, "response", None), "status_code", None) == 429
                or (
                    getattr(getattr(exc, "response", None), "status_code", 0) is not None
                    and 500 <= int(getattr(getattr(exc, "response", None), "status_code", 0) or 0) < 600
                )
                or exc.__class__.__name__ in {"ConnectionError", "Timeout"}
            ),
            operation="ocr_space_post",
        )
        return response.json(), time.monotonic() - started

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        if self.mock_mode:
            return self.load_mock_json(raw_dir)
        self.require_credentials()
        ensure_dir(raw_dir)

        pages = pdf_page_count(pdf_path)
        pages_per_request = int(self.config.get("pages_per_request", 3))
        max_bytes = int(self.config.get("max_file_bytes", 1_000_000))
        chunks = split_pdf(pdf_path, raw_dir / "chunks", pages_per_request)

        responses: list[str] = []
        total_latency = 0.0
        total_provider_ms = 0.0
        page_offset = 0

        for idx, (chunk, start_zero, chunk_pages) in enumerate(chunks):
            size = chunk.stat().st_size
            if size > max_bytes:
                raise ToolExecutionError(
                    f"OCR.Space free-tier chunk {chunk.name} is {size} bytes, above "
                    f"configured {max_bytes}. Reduce pages_per_request or compress the PDF."
                )
            payload, latency = self._post_chunk(chunk)
            total_latency += latency
            try:
                total_provider_ms += float(payload.get("ProcessingTimeInMilliseconds") or 0)
            except Exception:
                pass

            wrapped = {
                "page_offset": int(start_zero),
                "chunk_pages": int(chunk_pages),
                "response": payload,
            }
            target = raw_dir / f"response_{idx:03d}.json"
            write_json(target, wrapped)
            responses.append(str(target))

            if payload.get("IsErroredOnProcessing"):
                raise ToolExecutionError(
                    f"OCR.Space processing error: {payload.get('ErrorMessage')} "
                    f"{payload.get('ErrorDetails')}"
                )

        manifest = raw_dir / "manifest.json"
        write_json(manifest, {"responses": responses})
        return RawToolResult(
            primary_artifact=str(manifest),
            artifacts=responses + [str(manifest)] + [str(c[0]) for c in chunks],
            metadata={
                "mock": False,
                "pages": pages,
                "requests": len(chunks),
                "latency_seconds": total_latency,
                "processing_seconds": total_provider_ms / 1000.0 if total_provider_ms else None,
                "estimated_cost_usd": 0.0,
                "actual_cost_usd": 0.0,
                "quota_basis": "OCR.Space Free API; Engine 3 uses its own free conversion quota",
            },
        )

    def _parse_wrapper(
        self,
        wrapper: dict[str, Any],
        pages_by_num: dict[int, Page],
        page_sizes: list[tuple[float, float]],
        order: int,
    ) -> int:
        offset = int(wrapper.get("page_offset", 0) or 0)
        payload = wrapper.get("response", wrapper)
        results = payload.get("ParsedResults") or []
        pixels_per_point = float(self.config.get("overlay_pdf_pixels_per_point", 2.0))
        max_block_chars = max(128, int(self.config.get("parsed_text_max_block_chars", 4000)))

        for local_idx, parsed in enumerate(results):
            page_no = offset + local_idx + 1
            width, height = (
                page_sizes[page_no - 1]
                if 0 <= page_no - 1 < len(page_sizes)
                else (1.0, 1.0)
            )
            page = pages_by_num.setdefault(
                page_no, Page(page_number=page_no, width=width, height=height)
            )
            text = str(parsed.get("ParsedText") or "")
            overlay = parsed.get("TextOverlay") or {}
            overlay_lines = overlay.get("Lines") or []
            line_blocks = 0
            for line_index, line in enumerate(overlay_lines):
                line_text = _overlay_line_text(line)
                if not line_text:
                    continue
                bbox, raw_pixel_bbox = _overlay_line_bbox(
                    line,
                    page_width=width,
                    page_height=height,
                    pixels_per_point=pixels_per_point,
                )
                eid = f"ocrspace_p{page_no}_line_{line_index:04d}"
                page.text_blocks.append(
                    TextBlock(
                        element_id=eid,
                        page_number=page_no,
                        bbox=bbox,
                        raw_text=line_text,
                        block_type="paragraph",
                        order_index=order,
                        provenance={
                            "source": "TextOverlay.Lines",
                            "overlay_line_index": line_index,
                            "raw_pixel_bbox": raw_pixel_bbox,
                            "pixel_canvas": {
                                "width": width * pixels_per_point,
                                "height": height * pixels_per_point,
                                "pixels_per_pdf_point": pixels_per_point,
                            },
                            "overlay_message": overlay.get("Message"),
                        },
                    )
                )
                page.reading_order.append(eid)
                order += 1
                line_blocks += 1

            if line_blocks == 0:
                segments = _parsed_text_segments(text, max_block_chars)
                for segment_index, segment in enumerate(segments):
                    eid = f"ocrspace_p{page_no}_segment_{segment_index:04d}"
                    page.text_blocks.append(
                        TextBlock(
                            element_id=eid,
                            page_number=page_no,
                            bbox=None,
                            raw_text=segment,
                            block_type="paragraph",
                            order_index=order,
                            provenance={
                                "source": "ParsedText.segment",
                                "segment_index": segment_index,
                                "segment_count": len(segments),
                                "segmentation_policy": "blank_lines_then_bounded_chunks_v1",
                                "max_block_chars": max_block_chars,
                                "overlay_message": overlay.get("Message"),
                            },
                        )
                    )
                    page.reading_order.append(eid)
                    order += 1

            explicit_math = delimited_math_candidates(text)
            for formula_index, candidate in enumerate(explicit_math):
                eid = f"ocrspace_p{page_no}_math_formula_{formula_index:04d}"
                page.formulas.append(
                    Formula(
                        element_id=eid,
                        page_number=page_no,
                        bbox=None,
                        raw_text=candidate.latex,
                        latex=candidate.latex,
                        formula_type="display",
                        order_index=order,
                        provenance={
                            "source": "ParsedText.explicit_delimited_latex_v1",
                            "source_span": [candidate.start, candidate.end],
                            "wrapper": candidate.wrapper,
                            "gt_independent": True,
                        },
                    )
                )
                page.reading_order.append(eid)
                order += 1

            for group_index, group in enumerate(
                numbered_delimited_math_groups(text)
            ):
                latex = r" \quad ".join(candidate.latex for candidate in group)
                eid = f"ocrspace_p{page_no}_math_group_{group_index:04d}"
                page.formulas.append(
                    Formula(
                        element_id=eid,
                        page_number=page_no,
                        bbox=None,
                        raw_text=latex,
                        latex=latex,
                        formula_type="display",
                        order_index=order,
                        provenance={
                            "source": "ParsedText.numbered_display_group_v1",
                            "source_spans": [
                                [candidate.start, candidate.end]
                                for candidate in group
                            ],
                            "group_size": len(group),
                            "gt_independent": True,
                        },
                    )
                )
                page.reading_order.append(eid)
                order += 1

            for formula_index, (raw_formula, start, end) in enumerate(
                _linear_formula_candidates(text)
            ):
                eid = f"ocrspace_p{page_no}_chem_formula_{formula_index:03d}"
                page.chemical_objects.append(
                    ChemicalObject(
                        element_id=eid,
                        page_number=page_no,
                        bbox=None,
                        subtype="linear_formula",
                        raw_formula=raw_formula,
                        order_index=order,
                        provenance={
                            "source": "ParsedText.explicit_linear_formula",
                            "source_span": [start, end],
                            "recognition_policy": "strict_element_syntax_v1",
                            "chemical_correction_applied": False,
                        },
                    )
                )
                page.reading_order.append(eid)
                order += 1

            structures = _markdown_chemical_structures(text)
            structure_bboxes, structure_associations = _structure_overlay_bboxes(
                structures,
                overlay_lines,
                page_width=width,
                page_height=height,
                pixels_per_point=pixels_per_point,
            )
            for structure_index, structure in enumerate(structures):
                eid = f"ocrspace_p{page_no}_chem_structure_{structure_index:03d}"
                page.chemical_objects.append(
                    ChemicalObject(
                        element_id=eid,
                        page_number=page_no,
                        bbox=structure_bboxes[structure_index],
                        subtype="structure",
                        raw_formula=structure.expression,
                        text_labels=list(structure.text_labels),
                        asset_path=structure.source_url,
                        order_index=order,
                        provenance={
                            "source": "ParsedText.Markdown.CodeCogs.chem",
                            "parsed_text_line_index": structure.line_index,
                            "row_label": structure.row_label,
                            "alt_text": structure.alt_text,
                            "source_url": structure.source_url,
                            "asset_reference_kind": "remote_renderable_formula_uri",
                            "bbox_policy": "overlay_table_rows_v1",
                            **structure_associations[structure_index],
                        },
                    )
                )
                page.reading_order.append(eid)
                order += 1

            for ti, rows in enumerate(_markdown_tables(text)):
                ncols = max((len(r) for r in rows), default=0)
                cells: list[TableCell] = []
                for ri, row in enumerate(rows):
                    for ci in range(ncols):
                        cells.append(
                            TableCell(
                                row_index=ri,
                                column_index=ci,
                                text=row[ci] if ci < len(row) else "",
                                is_header=(ri == 0),
                            )
                        )
                tid = f"ocrspace_p{page_no}_table_{ti:03d}"
                page.tables.append(
                    Table(
                        element_id=tid,
                        page_number=page_no,
                        rows=len(rows),
                        columns=ncols,
                        cells=cells,
                        order_index=order,
                        provenance={"source": "Engine3 Markdown table"},
                    )
                )
                order += 1
        return order

    def standardize(self, raw_result, raw_dir, assets_dir, *, document_id, pdf_path):
        page_sizes = pdf_page_sizes(pdf_path)
        pages_by_num: dict[int, Page] = {}
        order = 0

        if raw_result.metadata.get("mock"):
            wrappers = [read_json(raw_dir / "mock_response.json")]
        else:
            manifest = read_json(Path(raw_result.primary_artifact or ""))
            wrappers = [read_json(Path(p)) for p in manifest.get("responses") or []]

        for wrapper in wrappers:
            order = self._parse_wrapper(wrapper, pages_by_num, page_sizes, order)

        if not pages_by_num:
            raise ToolOutputParseError("OCR.Space response contained no ParsedResults")

        return StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path),
            tool=self.tool_metadata(),
            pages=[pages_by_num[k] for k in sorted(pages_by_num)],
            metadata={"cloud_execution": raw_result.metadata},
        )
