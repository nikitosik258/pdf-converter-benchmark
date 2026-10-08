from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_benchmark.utils.io import ensure_dir


def sha256_file(path: str | Path) -> str:
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha256_files(paths: list[Path], *, root: Path) -> str:
    """Hash source files with portable, project-relative names."""
    h = hashlib.sha256()
    root = root.resolve()
    for path in sorted((Path(path).resolve() for path in paths), key=str):
        relative = path.relative_to(root).as_posix()
        h.update(relative.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def sha256_tree(root: Path, *, project_root: Path, pattern: str = "*.py") -> str:
    return sha256_files(
        [
            path
            for path in root.rglob(pattern)
            if path.is_file() and "__pycache__" not in path.parts
        ],
        root=project_root,
    )


def build_stage_fingerprints(
    *,
    tool: str,
    source_sha256: str,
    tool_config_sha256: str,
    raw_acquisition_version: str,
    standardization_version: str,
    standardization_code_sha256: str,
    normalization_version: str,
    normalization_config_sha256: str,
    normalization_code_sha256: str,
    evaluation_version: str,
    matching_config_sha256: str,
    scoring_config_sha256: str,
    evaluation_code_sha256: str,
    ground_truth_sha256: str,
    object_manifest_sha256: str,
) -> dict[str, str]:
    """Build independent fingerprints for every persisted pipeline stage."""
    return {
        "raw_acquisition": sha256_json(
            {
                "stage": "raw_acquisition",
                "version": raw_acquisition_version,
                "tool": tool,
                "source_sha256": source_sha256,
                "tool_config_sha256": tool_config_sha256,
            }
        ),
        "standardization": sha256_json(
            {
                "stage": "standardization",
                "version": standardization_version,
                "tool": tool,
                "code_sha256": standardization_code_sha256,
            }
        ),
        "normalization": sha256_json(
            {
                "stage": "normalization",
                "version": normalization_version,
                "config_sha256": normalization_config_sha256,
                "code_sha256": normalization_code_sha256,
            }
        ),
        "evaluation": sha256_json(
            {
                "stage": "evaluation",
                "version": evaluation_version,
                "matching_config_sha256": matching_config_sha256,
                "scoring_config_sha256": scoring_config_sha256,
                "code_sha256": evaluation_code_sha256,
                "ground_truth_sha256": ground_truth_sha256,
                "object_manifest_sha256": object_manifest_sha256,
            }
        ),
    }


@dataclass(frozen=True)
class CacheCompatibility:
    raw_compatible: bool
    standardized_compatible: bool
    normalized_compatible: bool
    evaluation_compatible: bool
    reasons: list[str]


@dataclass(frozen=True)
class PairCache:
    root: Path

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def standardized(self) -> Path:
        return self.root / "standardized.json"

    @property
    def normalized(self) -> Path:
        return self.root / "normalized.json"

    @property
    def raw_snapshot(self) -> Path:
        return self.root / "raw_result_snapshot.json"

    @property
    def adapter_result(self) -> Path:
        return self.root / "adapter_result.json"

    @property
    def adapter_run(self) -> Path:
        return self.root / "run.json"

    @property
    def cache_manifest(self) -> Path:
        return self.root / "cache_manifest.json"

    @property
    def matches(self) -> Path:
        return self.root / "matches.json"

    @property
    def object_evaluation(self) -> Path:
        return self.root / "object_evaluation.json"

    @property
    def pair_result(self) -> Path:
        return self.root / "pair_result.json"

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    def ensure(self) -> "PairCache":
        ensure_dir(self.root)
        ensure_dir(self.raw_dir)
        ensure_dir(self.assets_dir)
        return self


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cache_compatibility(
    cache: PairCache,
    *,
    source_sha256: str,
    tool_config_sha256: str,
    stage_fingerprints: dict[str, str],
) -> CacheCompatibility:
    manifest = read_json_if_exists(cache.cache_manifest)
    if not manifest:
        return CacheCompatibility(False, False, False, False, ["cache_manifest_missing"])

    reasons: list[str] = []
    if manifest.get("source_sha256") != source_sha256:
        reasons.append("source_sha256_changed")
    if manifest.get("tool_config_sha256") != tool_config_sha256:
        reasons.append("tool_config_changed")

    cached_stages = manifest.get("stage_fingerprints")
    if not isinstance(cached_stages, dict):
        # Legacy manifests did record enough provenance to validate raw. They
        # did not identify standardization code, so only raw can be reused.
        raw_compatible = not reasons
        reasons.extend(
            [
                "standardization_fingerprint_missing",
                "normalization_fingerprint_missing",
                "evaluation_fingerprint_missing",
            ]
        )
        return CacheCompatibility(raw_compatible, False, False, False, reasons)

    stage_matches: dict[str, bool] = {}
    for stage, expected in stage_fingerprints.items():
        actual = cached_stages.get(stage)
        stage_matches[stage] = actual == expected
        if actual is None:
            reasons.append(f"{stage}_fingerprint_missing")
        elif actual != expected:
            reasons.append(f"{stage}_fingerprint_changed")

    raw_compatible = not {
        "source_sha256_changed",
        "tool_config_changed",
    }.intersection(reasons) and stage_matches["raw_acquisition"]
    standardized_compatible = raw_compatible and stage_matches["standardization"]
    normalized_compatible = standardized_compatible and stage_matches["normalization"]
    evaluation_compatible = normalized_compatible and stage_matches["evaluation"]
    return CacheCompatibility(
        raw_compatible,
        standardized_compatible,
        normalized_compatible,
        evaluation_compatible,
        reasons,
    )
