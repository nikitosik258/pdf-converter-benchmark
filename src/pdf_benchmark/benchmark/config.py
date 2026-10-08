from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from .registry import TOOL_SPECS


class DocumentConfig(BaseModel):
    document_id: str
    filename: str
    path_candidates: list[str] = Field(default_factory=list)


class BenchmarkConfig(BaseModel):
    seed: int = 42
    output_root: str = "outputs/benchmark"
    ground_truth_dir: str = "ground_truth"
    object_manifest: str = "benchmark/object_manifest.json"
    normalization_config: str = "config/normalization.yaml"
    metrics_config: str = "config/metrics.yaml"
    matching_config: str = "config/matching.yaml"

    documents: list[DocumentConfig]
    tools: list[str]

    @model_validator(mode="after")
    def validate_tools(self):
        unknown = sorted(set(self.tools) - set(TOOL_SPECS))
        if unknown:
            raise ValueError(f"Unknown benchmark tools: {unknown}")
        ids = [d.document_id for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate document_id values in benchmark config")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> "BenchmarkConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        data = payload.get("benchmark", payload)
        docs_raw = data.get("documents", [])
        docs: list[dict[str, Any]] = []
        if isinstance(docs_raw, dict):
            for document_id, cfg in docs_raw.items():
                cfg = dict(cfg or {})
                cfg["document_id"] = document_id
                docs.append(cfg)
        else:
            docs = list(docs_raw)
        data = dict(data)
        data["documents"] = docs
        return cls.model_validate(data)

    def document_map(self) -> dict[str, DocumentConfig]:
        return {d.document_id: d for d in self.documents}


def resolve_document_path(project_root: Path, config: DocumentConfig) -> Path:
    candidates: list[Path] = []
    for raw in config.path_candidates:
        candidates.append(project_root / raw)
    candidates.extend(
        [
            project_root / "documents" / config.filename,
            project_root / config.filename,
            project_root / "data" / config.filename,
        ]
    )
    for path in candidates:
        if path.exists() and path.is_file():
            return path.resolve()

    # Last-resort deterministic search in known data roots only.
    for base in (project_root / "documents", project_root / "data"):
        if base.exists():
            found = sorted(base.rglob(config.filename))
            if found:
                return found[0].resolve()

    rendered = "\n  ".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"Could not locate {config.document_id} ({config.filename}). Tried:\n  {rendered}"
    )
