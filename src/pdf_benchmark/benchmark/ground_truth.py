from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pdf_benchmark.models import BBox


_GT_TYPE_ALIASES = {
    "text": "text",
    "ordinary_text": "text",
    "table": "table",
    "math": "math_formula",
    "formula": "math_formula",
    "math_formula": "math_formula",
    "chemical_formula": "chemical_formula",
    "chem_formula": "chemical_formula",
    "chemical_structure": "chemical_structure",
    "chem_structure": "chemical_structure",
    "image": "image",
    "diagram": "diagram",
    "chart": "diagram",
    "scheme": "diagram",
}

GTObjectType = Literal[
    "text",
    "table",
    "math_formula",
    "chemical_formula",
    "chemical_structure",
    "image",
    "diagram",
]


class GroundTruthObject(BaseModel):
    model_config = ConfigDict(extra="allow")

    object_id: str
    document_id: str
    page: int = Field(ge=1)
    object_type: GTObjectType
    bbox: BBox | None = None
    reference: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def inject_bbox(self):
        if self.bbox is not None and "bbox" not in self.reference:
            self.reference["bbox"] = self.bbox.model_dump(mode="json")
        return self


def _bbox(value: Any) -> BBox | None:
    if value is None:
        return None
    if isinstance(value, BBox):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return BBox(
            x_min=float(value[0]),
            y_min=float(value[1]),
            x_max=float(value[2]),
            y_max=float(value[3]),
        )
    if isinstance(value, dict):
        return BBox.model_validate(value)
    raise ValueError(f"Unsupported bbox representation: {value!r}")


def _canonical_type(payload: dict[str, Any]) -> str:
    raw = (
        payload.get("object_type")
        or payload.get("type")
        or payload.get("category")
        or ""
    )
    raw = str(raw).strip().lower()
    if raw == "chemistry":
        subtype = str(payload.get("subtype") or payload.get("chemistry_type") or "")
        if subtype in {"structure", "chemical_structure"}:
            return "chemical_structure"
        return "chemical_formula"
    try:
        return _GT_TYPE_ALIASES[raw]
    except KeyError as exc:
        raise ValueError(f"Unsupported Ground Truth object type: {raw!r}") from exc


_META_KEYS = {
    "object_id",
    "document_id",
    "page",
    "page_number",
    "object_type",
    "type",
    "category",
    "subtype",
    "chemistry_type",
    "bbox",
    "reference",
    "ground_truth",
}


def canonicalize_ground_truth(payload: dict[str, Any]) -> GroundTruthObject:
    object_type = _canonical_type(payload)
    page = int(payload.get("page", payload.get("page_number")))
    ref = payload.get("reference", payload.get("ground_truth"))
    if ref is None:
        ref = {k: v for k, v in payload.items() if k not in _META_KEYS}
    else:
        ref = dict(ref)

    # Common Prompt-4 field names -> evaluator field conventions.
    if object_type == "text":
        if "text" not in ref:
            ref["text"] = ref.get("normalized_text", ref.get("raw_text", ""))
    elif object_type == "math_formula":
        if "latex" not in ref:
            ref["latex"] = ref.get("normalized_latex", ref.get("plain_text", ""))
    elif object_type == "chemical_formula":
        if "formula" not in ref:
            ref["formula"] = ref.get(
                "normalized_formula",
                ref.get("raw_formula", ""),
            )
    elif object_type in {"image", "diagram", "chemical_structure"}:
        # extraction_success in reference is not required; only prediction uses it.
        pass

    bbox_value = payload.get("bbox", ref.get("bbox"))
    bbox = _bbox(bbox_value) if bbox_value is not None else None
    if bbox is not None:
        ref["bbox"] = bbox.model_dump(mode="json")

    return GroundTruthObject(
        object_id=str(payload["object_id"]),
        document_id=str(payload["document_id"]),
        page=page,
        object_type=object_type,
        bbox=bbox,
        reference=ref,
    )


def _load_json_payload(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(x) for x in payload]
    if isinstance(payload, dict):
        if isinstance(payload.get("objects"), list):
            return [dict(x) for x in payload["objects"]]
        if "object_id" in payload:
            return [payload]
    raise ValueError(f"Ground Truth file has unsupported structure: {path}")


def load_ground_truth(directory: str | Path) -> list[GroundTruthObject]:
    """Load GT from a stable primary file, with deterministic fallbacks.

    Preferred:
      ground_truth/objects.jsonl
      ground_truth/objects.json

    Supported fallback:
      ground_truth/**/objects.json
      ground_truth/**/*.json files containing a single object or {"objects": ...}
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"Ground Truth directory does not exist: {directory}")

    primary_jsonl = directory / "objects.jsonl"
    primary_json = directory / "objects.json"

    raw_objects: list[dict[str, Any]] = []
    if primary_jsonl.exists():
        for line_number, line in enumerate(
            primary_jsonl.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = line.strip()
            if not line:
                continue
            try:
                raw_objects.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at {primary_jsonl}:{line_number}"
                ) from exc
    elif primary_json.exists():
        raw_objects.extend(_load_json_payload(primary_json))
    else:
        candidates = sorted(
            p
            for p in directory.rglob("*.json")
            if p.name not in {"manifest.json", "metadata.json"}
        )
        for path in candidates:
            try:
                raw_objects.extend(_load_json_payload(path))
            except ValueError:
                # Ignore unrelated metadata JSON files in the fallback mode.
                continue

    if not raw_objects:
        raise ValueError(
            f"No Ground Truth objects found in {directory}. "
            "Create objects.jsonl or objects.json."
        )

    objects = [canonicalize_ground_truth(p) for p in raw_objects]
    ids = [obj.object_id for obj in objects]
    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    if duplicates:
        raise ValueError(f"Duplicate Ground Truth object_id values: {duplicates}")
    return objects


def load_object_manifest(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("objects", [])
    if not isinstance(payload, list):
        raise ValueError("Object manifest must contain a JSON list or {'objects': [...]}")
    return [dict(x) for x in payload]


def validate_ground_truth_manifest(
    objects: list[GroundTruthObject],
    manifest: list[dict[str, Any]],
    *,
    selected_documents: set[str] | None = None,
) -> None:
    expected = {
        str(item["object_id"]): item
        for item in manifest
        if selected_documents is None
        or str(item["document_id"]) in selected_documents
    }
    actual = {
        obj.object_id: obj
        for obj in objects
        if selected_documents is None
        or obj.document_id in selected_documents
    }

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing={missing}")
        if extra:
            parts.append(f"extra={extra}")
        raise ValueError("Ground Truth/object manifest mismatch: " + "; ".join(parts))

    for object_id, item in expected.items():
        obj = actual[object_id]
        if obj.document_id != str(item["document_id"]):
            raise ValueError(f"{object_id}: document_id mismatch")
        if obj.page != int(item["page"]):
            raise ValueError(f"{object_id}: page mismatch")
        expected_type = _GT_TYPE_ALIASES.get(
            str(item["object_type"]).lower(),
            str(item["object_type"]).lower(),
        )
        if obj.object_type != expected_type:
            raise ValueError(
                f"{object_id}: object_type={obj.object_type!r}, expected={expected_type!r}"
            )
