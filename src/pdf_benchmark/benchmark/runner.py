from __future__ import annotations

import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.evaluation.models import (
    EvaluationObjectInput,
    ObjectEvaluationResult,
)
from pdf_benchmark.evaluation.scoring import ScoringConfig
from pdf_benchmark.evaluation.storage import save_evaluation_report
from pdf_benchmark.models import StandardizedDocument
from pdf_benchmark.normalization.pipeline import (
    NormalizationConfig,
    normalize_document,
)
from pdf_benchmark.utils.io import ensure_dir, write_json
from pdf_benchmark.utils.resources import ResourceMonitor

from .cache import (
    PairCache,
    build_stage_fingerprints,
    cache_compatibility,
    read_json_if_exists,
    sha256_file,
    sha256_files,
    sha256_json,
    sha256_tree,
)
from .config import BenchmarkConfig, resolve_document_path
from .ground_truth import (
    GroundTruthObject,
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)
from .matching import MatchingConfig, match_document
from .registry import (
    TOOL_SPECS,
    environment_python,
    marker_runtime_env,
)
from .results import build_run_report


EVALUATION_PIPELINE_VERSION = "2"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id(seed: int) -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"_s{seed}_"
        + uuid.uuid4().hex[:8]
    )


def seed_everything(seed: int) -> None:
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass


def _tool_config(project_root: Path, tool: str) -> tuple[dict[str, Any], Path]:
    spec = TOOL_SPECS[tool]
    path = project_root / "config" / "tools" / spec.config_filename
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(payload.get("config", payload)), path


def _git_commit(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or None if result.returncode == 0 else None
    except Exception:
        return None


def _worker_command(
    project_root: Path,
    *,
    tool: str,
    pdf_path: Path,
    document_id: str,
    output_dir: Path,
    raw_cache_dir: Path | None,
    seed: int,
    standardize_only: bool,
    mock_cloud: bool,
) -> tuple[list[str], dict[str, str]]:
    spec = TOOL_SPECS[tool]
    python_exe = environment_python(project_root, spec.environment)
    script = project_root / "scripts" / "benchmark_worker.py"
    cmd = [
        str(python_exe),
        str(script),
        "--tool",
        tool,
        "--pdf",
        str(pdf_path),
        "--document-id",
        document_id,
        "--output-dir",
        str(output_dir),
        "--seed",
        str(seed),
    ]
    if standardize_only:
        cmd.append("--standardize-only")
        if raw_cache_dir is not None:
            cmd.extend(["--raw-cache-dir", str(raw_cache_dir)])
    if mock_cloud:
        cmd.append("--mock-cloud")

    env = os.environ.copy()
    env["PDF_BENCHMARK_SEED"] = str(seed)
    env["PYTHONHASHSEED"] = str(seed)
    if tool == "marker":
        env.update(marker_runtime_env(project_root))
    return cmd, env


def _run_worker(
    project_root: Path,
    **kwargs,
) -> tuple[int, float]:
    cmd, env = _worker_command(project_root, **kwargs)
    started = time.monotonic()
    completed = subprocess.run(
        cmd,
        cwd=project_root,
        env=env,
        check=False,
    )
    return completed.returncode, time.monotonic() - started


def _normalized_path_write(document: StandardizedDocument, path: Path) -> None:
    write_json(path, document.model_dump(mode="json"))


def _copy_standardized_cache(source: PairCache, target: PairCache) -> None:
    """Copy a reusable standardized stage together with its local assets."""
    if source.root == target.root:
        return
    shutil.copy2(source.standardized, target.standardized)
    if source.assets_dir.is_dir():
        shutil.copytree(source.assets_dir, target.assets_dir, dirs_exist_ok=True)


def _load_matching_config(path: Path) -> MatchingConfig:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return MatchingConfig.model_validate(payload.get("matching", payload))


def _pair_object_results(
    *,
    tool: str,
    document_id: str,
    matches,
    framework: EvaluationFramework,
) -> list[ObjectEvaluationResult]:
    results = []
    for match in matches:
        request = EvaluationObjectInput(
            tool=tool,
            document_id=document_id,
            page=match.page,
            object_id=match.object_id,
            object_type=match.object_type,
            reference=match.reference,
            prediction=match.prediction,
            context=match.context,
        )
        results.append(framework.evaluate_object(request))
    return results


def _save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


class BenchmarkRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        config: BenchmarkConfig,
        seed: int | None = None,
        force_rerun: bool = False,
        force_api: bool = False,
        reuse_stale_raw: bool = False,
        fail_fast: bool = False,
        mock_cloud: bool = False,
    ):
        self.project_root = project_root.resolve()
        self.config = config
        self.seed = int(seed if seed is not None else config.seed)
        self.force_rerun = force_rerun
        self.force_api = force_api
        self.reuse_stale_raw = reuse_stale_raw
        self.fail_fast = fail_fast
        self.mock_cloud = mock_cloud

        seed_everything(self.seed)

        self.output_root = ensure_dir(self.project_root / config.output_root)
        self.cache_root = ensure_dir(self.output_root / "cache")

        self.normalization_config = NormalizationConfig.from_yaml(
            self.project_root / config.normalization_config
        )
        self.scoring_config = ScoringConfig.from_yaml(
            self.project_root / config.metrics_config
        )
        self.matching_config = _load_matching_config(
            self.project_root / config.matching_config
        )

        self.ground_truth = load_ground_truth(
            self.project_root / config.ground_truth_dir
        )
        self.object_manifest = load_object_manifest(
            self.project_root / config.object_manifest
        )

        source_root = self.project_root / "src" / "pdf_benchmark"
        self._normalization_code_sha256 = sha256_tree(
            source_root / "normalization",
            project_root=self.project_root,
        )
        evaluation_files = list((source_root / "evaluation").rglob("*.py"))
        evaluation_files.extend(
            [
                source_root / "benchmark" / "matching.py",
                source_root / "benchmark" / "ground_truth.py",
            ]
        )
        self._evaluation_code_sha256 = sha256_files(
            evaluation_files,
            root=self.project_root,
        )
        self._ground_truth_sha256 = sha256_tree(
            self.project_root / config.ground_truth_dir,
            project_root=self.project_root,
            pattern="*",
        )
        self._object_manifest_sha256 = sha256_file(
            self.project_root / config.object_manifest
        )
        self._matching_config_sha256 = sha256_json(
            self.matching_config.model_dump(mode="json")
        )
        self._scoring_config_sha256 = sha256_json(
            self.scoring_config.model_dump(mode="json")
        )

    def _gt_for_document(self, document_id: str) -> list[GroundTruthObject]:
        return [
            x for x in self.ground_truth
            if x.document_id == document_id
        ]

    def _stage_fingerprints(
        self,
        *,
        tool: str,
        source_sha256: str,
        tool_config_sha256: str,
    ) -> dict[str, str]:
        spec = TOOL_SPECS[tool]
        source_root = self.project_root / "src" / "pdf_benchmark"
        standardization_files = [
            self.project_root / spec.adapter_module,
            source_root / "adapters" / "base.py",
            source_root / "models" / "schema.py",
            source_root / "benchmark" / "worker.py",
            source_root / "benchmark" / "runner.py",
            source_root / "standardization" / "__init__.py",
            source_root / "standardization" / "chemistry.py",
            source_root / "standardization" / "math.py",
        ]
        if spec.kind == "cloud":
            standardization_files.extend(
                [
                    source_root / "adapters" / "cloud" / "base.py",
                    source_root / "adapters" / "cloud" / "helpers.py",
                ]
            )
        standardization_code_sha256 = sha256_files(
            standardization_files,
            root=self.project_root,
        )
        return build_stage_fingerprints(
            tool=tool,
            source_sha256=source_sha256,
            tool_config_sha256=tool_config_sha256,
            raw_acquisition_version=spec.raw_acquisition_version,
            standardization_version=spec.standardization_version,
            standardization_code_sha256=standardization_code_sha256,
            normalization_version=self.normalization_config.version,
            normalization_config_sha256=self.normalization_config.fingerprint(),
            normalization_code_sha256=self._normalization_code_sha256,
            evaluation_version=EVALUATION_PIPELINE_VERSION,
            matching_config_sha256=self._matching_config_sha256,
            scoring_config_sha256=self._scoring_config_sha256,
            evaluation_code_sha256=self._evaluation_code_sha256,
            ground_truth_sha256=self._ground_truth_sha256,
            object_manifest_sha256=self._object_manifest_sha256,
        )

    def _plan_adapter_action(
        self,
        *,
        spec,
        raw_exists: bool,
        standardized_exists: bool,
        raw_compatible: bool,
        standardized_compatible: bool,
        incompatibility_reasons: list[str],
    ) -> tuple[str, list[str]]:
        notes: list[str] = []

        if spec.kind == "cloud":
            if standardized_compatible and standardized_exists and not self.force_rerun:
                return "reuse_standardized", notes

            if raw_exists:
                if not raw_compatible and not self.reuse_stale_raw and not self.force_api:
                    raise RuntimeError(
                        "Cloud raw acquisition cache inputs changed "
                        f"({incompatibility_reasons}). Refusing a silent API re-call. "
                        "Use --reuse-stale-raw to intentionally re-standardize the "
                        "existing vendor response, or --force-api to acquire fresh raw."
                    )
                if self.force_api:
                    return "run_adapter", ["force_api=true"]
                notes.append("cloud_api_not_recalled")
                if not raw_compatible:
                    notes.append("reusing_stale_raw_by_explicit_flag")
                elif not standardized_compatible:
                    notes.append("standardization_cache_invalidated")
                return "standardize_only", notes

            # No previous billable/raw call exists: first invocation is allowed.
            return "run_adapter", ["first_cloud_raw_call"]

        # Local tools.
        if standardized_compatible and standardized_exists and not self.force_rerun:
            return "reuse_standardized", notes
        if raw_exists and not self.force_rerun and raw_compatible:
            return "standardize_only", ["standardization_cache_invalidated"]
        return "run_adapter", notes

    def run_pair(
        self,
        *,
        tool: str,
        document_id: str,
        run_dir: Path,
    ) -> tuple[list[ObjectEvaluationResult], dict[str, Any]]:
        if tool not in TOOL_SPECS:
            raise KeyError(tool)
        spec = TOOL_SPECS[tool]
        doc_cfg = self.config.document_map()[document_id]
        pdf_path = resolve_document_path(self.project_root, doc_cfg)

        gt = self._gt_for_document(document_id)
        if not gt:
            raise ValueError(f"No Ground Truth objects for {document_id}")

        validate_ground_truth_manifest(
            self.ground_truth,
            self.object_manifest,
            selected_documents={document_id},
        )

        base_cache = PairCache(self.cache_root / tool / document_id).ensure()
        source_sha = sha256_file(pdf_path)
        tool_cfg, tool_cfg_path = _tool_config(self.project_root, tool)
        if self.mock_cloud and spec.kind == "cloud":
            tool_cfg = dict(tool_cfg)
            tool_cfg["mock"] = True
        config_sha = sha256_json(tool_cfg)

        stage_fingerprints = self._stage_fingerprints(
            tool=tool,
            source_sha256=source_sha,
            tool_config_sha256=config_sha,
        )
        pipeline_cache_id = sha256_json(stage_fingerprints)[:24]
        cache = PairCache(base_cache.versions_dir / pipeline_cache_id)

        candidate_caches: list[PairCache] = []
        if cache.root.exists():
            candidate_caches.append(cache)
        if base_cache.versions_dir.exists():
            candidate_caches.extend(
                PairCache(path)
                for path in sorted(base_cache.versions_dir.iterdir())
                if path.is_dir() and path != cache.root
            )
        candidate_caches.append(base_cache)

        candidate_compatibility = {
            candidate.root: cache_compatibility(
                candidate,
                source_sha256=source_sha,
                tool_config_sha256=config_sha,
                stage_fingerprints=stage_fingerprints,
            )
            for candidate in candidate_caches
        }
        standardized_source = next(
            (
                candidate
                for candidate in candidate_caches
                if candidate.standardized.exists()
                and candidate_compatibility[candidate.root].standardized_compatible
            ),
            None,
        )
        raw_source = next(
            (
                candidate
                for candidate in candidate_caches
                if candidate.raw_snapshot.exists()
                and candidate_compatibility[candidate.root].raw_compatible
            ),
            None,
        )
        stale_raw_source = next(
            (candidate for candidate in candidate_caches if candidate.raw_snapshot.exists()),
            None,
        )
        selected_compatibility = candidate_compatibility[
            (standardized_source or raw_source or stale_raw_source or base_cache).root
        ]
        raw_compatible = raw_source is not None
        standardized_compatible = standardized_source is not None

        cache.ensure()
        action, notes = self._plan_adapter_action(
            spec=spec,
            raw_exists=(raw_source is not None or stale_raw_source is not None),
            standardized_exists=standardized_source is not None,
            raw_compatible=raw_compatible,
            standardized_compatible=standardized_compatible,
            incompatibility_reasons=selected_compatibility.reasons,
        )

        pair_started = time.monotonic()
        adapter_wall_current = 0.0

        if action == "reuse_standardized":
            if standardized_source is None:  # pragma: no cover - planner invariant
                raise RuntimeError("No compatible standardized cache was selected")
            _copy_standardized_cache(standardized_source, cache)
        else:
            selected_raw_source = raw_source or stale_raw_source
            returncode, adapter_wall_current = _run_worker(
                self.project_root,
                tool=tool,
                pdf_path=pdf_path,
                document_id=document_id,
                output_dir=cache.root,
                raw_cache_dir=(
                    selected_raw_source.root
                    if action == "standardize_only" and selected_raw_source is not None
                    else None
                ),
                seed=self.seed,
                standardize_only=(action == "standardize_only"),
                mock_cloud=(self.mock_cloud and spec.kind == "cloud"),
            )
            if returncode != 0:
                raise RuntimeError(
                    f"Adapter worker failed for {tool}/{document_id} "
                    f"with exit code {returncode}"
                )
        if not cache.standardized.exists():
            raise FileNotFoundError(cache.standardized)

        metadata_source = next(
            (
                candidate
                for candidate in [cache, standardized_source, raw_source, stale_raw_source]
                if candidate is not None
                and (candidate.adapter_result.exists() or candidate.adapter_run.exists())
            ),
            cache,
        )
        adapter_result = read_json_if_exists(metadata_source.adapter_result) or {}
        adapter_run = read_json_if_exists(metadata_source.adapter_run) or {}
        adapter_has_stale_failure = (
            adapter_result.get("status") == "failed"
            or adapter_run.get("status") == "failed"
        )

        # In standardize_only mode the current worker has just re-standardized
        # an existing raw vendor response and returned exit code 0. The cached
        # adapter_result/run.json may still describe an older failed
        # standardization attempt. Treat that failure state as historical
        # metadata rather than as the status of the current invocation.
        if adapter_has_stale_failure and action in {"standardize_only", "reuse_standardized"}:
            notes.append(
                "stale_adapter_failure_metadata_ignored_for_valid_standardized_cache"
            )
        elif adapter_has_stale_failure:
            raise RuntimeError(
                f"Adapter reported failure for {tool}/{document_id}: "
                f"{adapter_result.get('error_message') or adapter_run.get('error_message')}"
            )

        standardized = StandardizedDocument.model_validate_json(
            cache.standardized.read_text(encoding="utf-8")
        )

        with ResourceMonitor(interval=0.05) as downstream_monitor:
            t0 = time.monotonic()
            normalized = normalize_document(
                standardized,
                self.normalization_config,
            )
            _normalized_path_write(normalized, cache.normalized)
            normalization_seconds = time.monotonic() - t0

            t0 = time.monotonic()
            matches = match_document(
                gt,
                normalized,
                config=self.matching_config,
            )
            write_json(
                cache.matches,
                [m.model_dump(mode="json") for m in matches],
            )
            matching_seconds = time.monotonic() - t0

            t0 = time.monotonic()
            framework = EvaluationFramework(self.scoring_config)
            object_results = _pair_object_results(
                tool=tool,
                document_id=document_id,
                matches=matches,
                framework=framework,
            )
            write_json(
                cache.object_evaluation,
                [x.model_dump(mode="json") for x in object_results],
            )
            evaluation_seconds = time.monotonic() - t0

        downstream_usage = downstream_monitor.result().model_dump(mode="json")
        total_seconds = time.monotonic() - pair_started

        # Cache manifest is written after a successful end-to-end pair.
        acquisition_cache = (
            cache
            if cache.raw_snapshot.exists()
            else (raw_source or stale_raw_source)
        )
        persisted_stage_fingerprints = dict(stage_fingerprints)
        if action == "standardize_only" and not raw_compatible:
            raw_manifest = (
                read_json_if_exists(acquisition_cache.cache_manifest)
                if acquisition_cache is not None
                else None
            ) or {}
            cached_stage_fingerprints = raw_manifest.get("stage_fingerprints") or {}
            persisted_stage_fingerprints["raw_acquisition"] = (
                cached_stage_fingerprints.get("raw_acquisition")
                or "legacy-"
                + sha256_json(
                    {
                        "source_sha256": raw_manifest.get("source_sha256"),
                        "tool_config_sha256": raw_manifest.get("tool_config_sha256"),
                    }
                )
            )

        cache_manifest = {
            "cache_schema_version": 2,
            "tool": tool,
            "tool_kind": spec.kind,
            "document_id": document_id,
            "source_pdf": str(pdf_path),
            "source_sha256": source_sha,
            "tool_config_path": str(tool_cfg_path),
            "tool_config_sha256": config_sha,
            "normalization_config_sha256": self.normalization_config.fingerprint(),
            "stage_fingerprints": persisted_stage_fingerprints,
            "raw_cache_dir": str(acquisition_cache.root) if acquisition_cache else None,
            "seed": self.seed,
            "standardized_path": str(cache.standardized),
            "normalized_path": str(cache.normalized),
            "updated_at": utc_now(),
            "mock_cloud": bool(self.mock_cloud and spec.kind == "cloud"),
        }
        write_json(cache.cache_manifest, cache_manifest)

        pair_result = {
            "tool": tool,
            "tool_kind": spec.kind,
            "document_id": document_id,
            "status": "success",
            "adapter_action": action,
            "resume_notes": notes,
            "cache_compatibility_before_run": {
                "raw": raw_compatible,
                "standardized": standardized_compatible,
                "normalized": selected_compatibility.normalized_compatible,
                "evaluation": selected_compatibility.evaluation_compatible,
                "reasons": selected_compatibility.reasons,
            },
            "stage_fingerprints": persisted_stage_fingerprints,
            "source_pdf": str(pdf_path),
            "source_sha256": source_sha,
            "cache_dir": str(cache.root),
            "paths": {
                "raw_dir": str(acquisition_cache.raw_dir) if acquisition_cache else None,
                "standardized": str(cache.standardized),
                "normalized": str(cache.normalized),
                "matches": str(cache.matches),
                "object_evaluation": str(cache.object_evaluation),
            },
            "stage_timings_seconds": {
                "adapter_current_invocation": adapter_wall_current,
                "normalization": normalization_seconds,
                "matching": matching_seconds,
                "evaluation": evaluation_seconds,
                "pair_total_current_invocation": total_seconds,
            },
            "tool_metadata": standardized.tool.model_dump(mode="json"),
            "adapter_version_snapshot": {
                "distribution": adapter_run.get("distribution")
                    or standardized.tool.distribution_name,
                "pinned_version": adapter_run.get("pinned_version"),
                "installed_version": adapter_run.get("installed_version")
                    or standardized.tool.version,
                "model_versions": standardized.tool.model_versions,
            },
            "adapter_resource_usage": (
                (
                    read_json_if_exists(acquisition_cache.adapter_result)
                    if acquisition_cache is not None
                    else None
                )
                or (
                    read_json_if_exists(acquisition_cache.adapter_run)
                    if acquisition_cache is not None
                    else None
                )
                or adapter_result
                or adapter_run
                or {}
            ).get("resource_usage", {}),
            "standardization_resource_usage_current_invocation": (
                adapter_result.get("resource_usage", {})
                if action == "standardize_only"
                else {}
            ),
            "adapter_raw_metadata": (
                (adapter_result.get("raw_result") or {}).get("metadata")
                or adapter_run.get("raw_metadata")
                or {}
            ),
            "downstream_resource_usage": downstream_usage,
            "object_count": len(object_results),
            "matched_count": sum(1 for m in matches if m.matched),
            "missing_count": sum(1 for m in matches if not m.matched),
            "seed": self.seed,
        }
        write_json(cache.pair_result, pair_result)

        # Run directory receives a lightweight immutable snapshot/reference.
        run_pair_dir = ensure_dir(run_dir / "pairs" / tool / document_id)
        write_json(run_pair_dir / "pair_result.json", pair_result)
        shutil.copy2(cache.object_evaluation, run_pair_dir / "object_evaluation.json")
        shutil.copy2(cache.matches, run_pair_dir / "matches.json")

        return object_results, pair_result

    def run(
        self,
        *,
        tools: list[str],
        documents: list[str],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        unknown_tools = sorted(set(tools) - set(self.config.tools))
        unknown_docs = sorted(set(documents) - set(self.config.document_map()))
        if unknown_tools:
            raise ValueError(f"Tools not enabled in benchmark config: {unknown_tools}")
        if unknown_docs:
            raise ValueError(f"Unknown documents: {unknown_docs}")

        validate_ground_truth_manifest(
            self.ground_truth,
            self.object_manifest,
            selected_documents=set(documents),
        )

        run_id = run_id or make_run_id(self.seed)
        run_dir = ensure_dir(self.output_root / "runs" / run_id)
        started_at = utc_now()
        all_results: list[ObjectEvaluationResult] = []
        pair_results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for tool in tools:
            for document_id in documents:
                try:
                    objects, pair = self.run_pair(
                        tool=tool,
                        document_id=document_id,
                        run_dir=run_dir,
                    )
                    all_results.extend(objects)
                    pair_results.append(pair)
                except Exception as exc:
                    failure = {
                        "tool": tool,
                        "document_id": document_id,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                    failures.append(failure)
                    pair_results.append(failure)
                    if self.fail_fast:
                        raise

        report = build_run_report(
            all_results,
            scoring_config=self.scoring_config,
        )
        report.metadata.update(
            {
                "run_id": run_id,
                "selected_tools": tools,
                "selected_documents": documents,
                "failures": failures,
                "seed": self.seed,
            }
        )
        metric_paths = save_evaluation_report(
            report,
            run_dir / "metrics",
        )

        _save_jsonl(
            run_dir / "pair_results.jsonl",
            pair_results,
        )

        tool_versions: dict[str, dict[str, Any]] = {}
        for pair in pair_results:
            if pair.get("status") != "success":
                continue
            tool_name = pair["tool"]
            tool_versions.setdefault(
                tool_name,
                pair.get("adapter_version_snapshot", {}),
            )

        run_manifest = {
            "schema_version": "1.0",
            "pipeline_version": "prompt11-v1",
            "run_id": run_id,
            "status": (
                "success"
                if not failures
                else ("partial" if all_results else "failed")
            ),
            "started_at": started_at,
            "finished_at": utc_now(),
            "project_root": str(self.project_root),
            "benchmark_config": self.config.model_dump(mode="json"),
            "seed": self.seed,
            "selected_tools": tools,
            "selected_documents": documents,
            "pair_count_expected": len(tools) * len(documents),
            "pair_count_success": len(pair_results) - len(failures),
            "pair_count_failed": len(failures),
            "object_result_count": len(all_results),
            "metric_paths": metric_paths,
            "failures": failures,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "git_commit": _git_commit(self.project_root),
            "tool_versions": tool_versions,
            "tool_environments": {
                name: {
                    "kind": TOOL_SPECS[name].kind,
                    "environment": TOOL_SPECS[name].environment,
                    "config_filename": TOOL_SPECS[name].config_filename,
                }
                for name in tools
            },
        }
        write_json(run_dir / "run_manifest.json", run_manifest)

        # Stable latest pointer without deleting historical runs.
        latest = self.output_root / "latest_run.txt"
        latest.write_text(run_id + "\n", encoding="utf-8")

        return run_manifest
