from __future__ import annotations

import csv
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from pdf_benchmark.benchmark.config import BenchmarkConfig, resolve_document_path
from pdf_benchmark.benchmark.ground_truth import (
    GroundTruthObject,
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)
from pdf_benchmark.benchmark.registry import TOOL_SPECS, environment_python
from pdf_benchmark.benchmark.runner import BenchmarkRunner
from pdf_benchmark.models import StandardizedDocument, Table
from pdf_benchmark.utils.io import ensure_dir, write_json


CATEGORY_NAMES = ["text", "table", "math", "chemistry", "image", "diagram"]
QUALITY_COLUMNS = {
    "text": "text_score",
    "table": "table_score",
    "math": "math_score",
    "chemistry": "chemistry_score",
    "image": "image_score",
    "diagram": "diagram_score",
}
CHEMISTRY_REPORTING_SCORES = (
    "chemistry_detection_score",
    "chemistry_structured_extraction_score",
)


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    tool: str | None = None
    document_id: str | None = None
    metric: str | None = None
    value: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "tool": self.tool,
            "document_id": self.document_id,
            "metric": self.metric,
            "value": self.value,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_experiment_id(seed: int) -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_s{seed}_" + uuid.uuid4().hex[:8]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_value(row.get(k)) for k in fields})


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _experiment_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(payload.get("experiment", payload))


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _find_placeholder(value: Any, path: str = "reference") -> list[str]:
    hits: list[str] = []
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"...", "todo", "tbd", "placeholder", "fill me", "???"}:
            hits.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            hits.extend(_find_placeholder(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            hits.extend(_find_placeholder(item, f"{path}[{i}]"))
    return hits


def validate_ground_truth_content(objects: list[GroundTruthObject], *, require_bbox_all: bool = True) -> list[Issue]:
    issues: list[Issue] = []
    for obj in objects:
        prefix = f"{obj.object_id} ({obj.document_id} p.{obj.page})"
        ref = obj.reference

        for hit in _find_placeholder(ref):
            issues.append(Issue("error", "gt_placeholder", f"{prefix}: placeholder at {hit}", document_id=obj.document_id))

        if require_bbox_all and obj.bbox is None:
            issues.append(Issue("error", "gt_missing_bbox", f"{prefix}: bbox is missing", document_id=obj.document_id))
        if obj.bbox is not None and (obj.bbox.x_min >= obj.bbox.x_max or obj.bbox.y_min >= obj.bbox.y_max):
            issues.append(Issue("error", "gt_invalid_bbox", f"{prefix}: bbox has non-positive area", document_id=obj.document_id))

        if obj.object_type == "text":
            if not str(ref.get("text") or ref.get("normalized_text") or ref.get("raw_text") or "").strip():
                issues.append(Issue("error", "gt_empty_text", f"{prefix}: text reference is empty", document_id=obj.document_id))
        elif obj.object_type == "table":
            try:
                table = Table.model_validate(ref)
                if table.rows <= 0 or table.columns <= 0 or not table.cells:
                    raise ValueError("table has no usable grid/cells")
                seen = set()
                for cell in table.cells:
                    key = (cell.row_index, cell.column_index, cell.row_span, cell.column_span)
                    if key in seen:
                        raise ValueError(f"duplicate cell key {key}")
                    seen.add(key)
                    if cell.row_index >= table.rows or cell.column_index >= table.columns:
                        raise ValueError(f"cell outside declared grid: {key}")
            except Exception as exc:
                issues.append(Issue("error", "gt_invalid_table", f"{prefix}: {exc}", document_id=obj.document_id))
        elif obj.object_type == "math_formula":
            if not str(ref.get("latex") or ref.get("normalized_latex") or "").strip():
                issues.append(Issue("error", "gt_empty_math", f"{prefix}: LaTeX reference is empty", document_id=obj.document_id))
        elif obj.object_type == "chemical_formula":
            if not str(ref.get("formula") or ref.get("normalized_formula") or ref.get("raw_formula") or "").strip():
                issues.append(Issue("error", "gt_empty_chem_formula", f"{prefix}: chemical formula is empty", document_id=obj.document_id))
        elif obj.object_type == "chemical_structure":
            labels = list(ref.get("text_labels") or [])
            caption = ref.get("caption")
            name = ref.get("name")
            if not labels and not caption and not name:
                issues.append(Issue("warning", "gt_structure_weak_annotation", f"{prefix}: no text_labels/name/caption; structure label metric may be weak", document_id=obj.document_id))
        elif obj.object_type == "image":
            if not any(ref.get(k) for k in ("reference_image", "asset_path", "image_path")):
                issues.append(Issue("warning", "gt_image_no_reference_asset", f"{prefix}: no reference image asset path recorded", document_id=obj.document_id))
        elif obj.object_type == "diagram":
            if not (ref.get("key_elements") or ref.get("text_elements") or ref.get("subfigures") or ref.get("caption")):
                issues.append(Issue("error", "gt_diagram_empty_annotation", f"{prefix}: diagram has no evaluable annotation", document_id=obj.document_id))
    return issues


def probe_environment(project_root: Path, tool: str) -> tuple[dict[str, Any] | None, str | None]:
    spec = TOOL_SPECS[tool]
    try:
        python_exe = environment_python(project_root, spec.environment)
    except Exception as exc:
        return None, str(exc)
    script = project_root / "scripts" / "probe_tool_environment.py"
    proc = subprocess.run(
        [str(python_exe), str(script), "--tool", tool],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    # Some third-party imports can emit deprecation notices to stdout before
    # our JSON payload. Parse the last non-empty JSON-looking line instead of
    # treating a harmless warning as a failed environment probe.
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line), None
            except Exception:
                pass
    try:
        return json.loads(proc.stdout), None
    except Exception as exc:
        return None, f"invalid probe JSON: {exc}; stdout={proc.stdout[:1000]!r}"


def probe_cloud_credentials(project_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        python_exe = environment_python(project_root, ".venv-cloud")
    except Exception as exc:
        return None, str(exc)
    proc = subprocess.run(
        [str(python_exe), str(project_root / "scripts" / "probe_cloud_credentials.py")],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    try:
        return json.loads(proc.stdout), None
    except Exception as exc:
        return None, f"invalid credential probe JSON: {exc}"


def run_preflight(project_root: Path, benchmark_config_path: Path, experiment_config_path: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    benchmark_cfg = BenchmarkConfig.from_yaml(benchmark_config_path)
    exp_cfg = _experiment_config(experiment_config_path)
    issues: list[Issue] = []

    pdf_rows: list[dict[str, Any]] = []
    expected_pages = exp_cfg.get("expected_pages", {})
    total_pages = 0
    try:
        import pymupdf
    except Exception as exc:
        pymupdf = None
        issues.append(Issue("error", "pymupdf_unavailable", f"Cannot inspect PDFs: {exc}"))

    for doc in benchmark_cfg.documents:
        try:
            path = resolve_document_path(project_root, doc)
            if path.stat().st_size <= 0:
                raise ValueError("file is empty")
            if pymupdf is None:
                raise RuntimeError("PyMuPDF unavailable")
            with pymupdf.open(path) as pdf:
                pages = len(pdf)
                if pdf.needs_pass:
                    raise ValueError("PDF is encrypted/password protected")
                if pages <= 0:
                    raise ValueError("PDF has no pages")
                # Touch first/last page so broken xrefs/page trees are noticed.
                _ = pdf[0].rect
                _ = pdf[-1].rect
            total_pages += pages
            expected = expected_pages.get(doc.document_id)
            if expected is not None and pages != int(expected):
                issues.append(Issue("error", "pdf_page_count_mismatch", f"{doc.document_id}: {pages} pages, expected {expected}", document_id=doc.document_id, value=pages))
            pdf_rows.append({
                "document_id": doc.document_id,
                "path": str(path),
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "pages": pages,
                "sha256": _sha256(path),
            })
        except Exception as exc:
            issues.append(Issue("error", "pdf_invalid_or_missing", f"{doc.document_id}: {exc}", document_id=doc.document_id))

    expected_total = int(exp_cfg.get("expected_total_pages", 0) or 0)
    if expected_total and total_pages != expected_total:
        issues.append(Issue("error", "pdf_total_pages_mismatch", f"Corpus total is {total_pages}, expected {expected_total}", value=total_pages))

    gt_rows: list[dict[str, Any]] = []
    gt_hashes: dict[str, str] = {}
    try:
        gt_dir = project_root / benchmark_cfg.ground_truth_dir
        objects = load_ground_truth(gt_dir)
        manifest = load_object_manifest(project_root / benchmark_cfg.object_manifest)
        validate_ground_truth_manifest(objects, manifest)
        issues.extend(validate_ground_truth_content(objects, require_bbox_all=bool(exp_cfg.get("require_bbox_all_gt_objects", True))))
        gt_rows = [
            {
                "object_id": x.object_id,
                "document_id": x.document_id,
                "page": x.page,
                "object_type": x.object_type,
                "has_bbox": x.bbox is not None,
            }
            for x in objects
        ]
        for path in sorted(gt_dir.rglob("*")):
            if path.is_file():
                gt_hashes[str(path.relative_to(project_root))] = _sha256(path)
        manifest_path = project_root / benchmark_cfg.object_manifest
        gt_hashes[str(manifest_path.relative_to(project_root))] = _sha256(manifest_path)
    except Exception as exc:
        issues.append(Issue("error", "ground_truth_invalid", str(exc)))
        objects = []

    environment_rows: list[dict[str, Any]] = []
    for tool in benchmark_cfg.tools:
        info, error = probe_environment(project_root, tool)
        if error:
            issues.append(Issue("error", "adapter_environment_not_ready", f"{tool}: {error}", tool=tool))
            environment_rows.append({"tool": tool, "ok": False, "error": error})
        else:
            environment_rows.append({"tool": tool, "ok": True, **(info or {})})
            if not (info or {}).get("package_version"):
                issues.append(Issue("warning", "adapter_version_unknown", f"{tool}: installed package version could not be determined", tool=tool))

    credentials, credentials_error = probe_cloud_credentials(project_root)
    if credentials_error:
        issues.append(Issue("error", "cloud_credential_probe_failed", credentials_error))
        credentials = {}
    else:
        active_cloud_tools = [
            tool for tool in benchmark_cfg.tools
            if TOOL_SPECS[tool].kind == "cloud"
        ]
        for tool in active_cloud_tools:
            state = (credentials or {}).get(tool, {})
            if not state.get("ready", False):
                issues.append(Issue("error", "cloud_credentials_missing", f"{tool}: {state.get('detail', 'credentials not ready')}", tool=tool))

    report = {
        "schema_version": "1.0",
        "created_at": utc_now(),
        "status": "pass" if not any(i.severity == "error" for i in issues) else "fail",
        "project_root": str(project_root),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "pdfs": pdf_rows,
        "total_pages": total_pages,
        "ground_truth_object_count": len(objects),
        "ground_truth_index": gt_rows,
        "ground_truth_hashes": gt_hashes,
        "environments": environment_rows,
        "cloud_credentials": credentials,
        "issues": [i.as_dict() for i in issues],
        "error_count": sum(i.severity == "error" for i in issues),
        "warning_count": sum(i.severity == "warning" for i in issues),
    }
    return report


def _expected_objects_by_doc(manifest: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in manifest:
        out[str(item["document_id"])].append(item)
    return dict(out)


def validate_tool_run(project_root: Path, run_dir: Path, tool: str, benchmark_cfg: BenchmarkConfig) -> dict[str, Any]:
    issues: list[Issue] = []
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return {
            "tool": tool,
            "status": "corrupt",
            "issues": [Issue("error", "missing_run_manifest", str(manifest_path), tool=tool).as_dict()],
            "error_count": 1,
            "warning_count": 0,
        }

    try:
        run_manifest = read_json(manifest_path)
    except Exception as exc:
        return {
            "tool": tool,
            "status": "corrupt",
            "issues": [Issue("error", "invalid_run_manifest", str(exc), tool=tool).as_dict()],
            "error_count": 1,
            "warning_count": 0,
        }

    if run_manifest.get("selected_tools") != [tool]:
        issues.append(Issue("error", "wrong_tool_in_run", f"selected_tools={run_manifest.get('selected_tools')}", tool=tool))

    expected_docs = [d.document_id for d in benchmark_cfg.documents]
    if sorted(run_manifest.get("selected_documents") or []) != sorted(expected_docs):
        issues.append(Issue("error", "incomplete_document_selection", f"expected {expected_docs}", tool=tool))

    pair_path = run_dir / "pair_results.jsonl"
    try:
        pair_rows = read_jsonl(pair_path)
    except Exception as exc:
        pair_rows = []
        issues.append(Issue("error", "invalid_pair_results", str(exc), tool=tool))

    pair_by_doc = {str(r.get("document_id")): r for r in pair_rows}
    object_manifest = load_object_manifest(project_root / benchmark_cfg.object_manifest)
    expected_by_doc = _expected_objects_by_doc(object_manifest)

    total_objects = 0
    for doc_id in expected_docs:
        pair = pair_by_doc.get(doc_id)
        if not pair:
            issues.append(Issue("error", "missing_pair_result", f"{tool}/{doc_id}: pair result missing", tool=tool, document_id=doc_id))
            continue
        if pair.get("status") != "success":
            issues.append(Issue("error", "failed_run", f"{tool}/{doc_id}: {pair.get('error_type')}: {pair.get('error_message')}", tool=tool, document_id=doc_id))
            continue

        expected_objects = expected_by_doc.get(doc_id, [])
        if int(pair.get("object_count", -1)) != len(expected_objects):
            issues.append(Issue("error", "object_count_mismatch", f"{tool}/{doc_id}: object_count={pair.get('object_count')}, expected={len(expected_objects)}", tool=tool, document_id=doc_id))

        matched = int(pair.get("matched_count", 0))
        missing = int(pair.get("missing_count", 0))
        if matched + missing != len(expected_objects):
            issues.append(Issue("error", "match_accounting_mismatch", f"{tool}/{doc_id}: matched+missing={matched+missing}, expected={len(expected_objects)}", tool=tool, document_id=doc_id))

        paths = pair.get("paths") or {}
        required_paths = ["standardized", "normalized", "matches", "object_evaluation"]
        for key in required_paths:
            p = Path(str(paths.get(key) or ""))
            if not p.exists():
                issues.append(Issue("error", "missing_pair_artifact", f"{tool}/{doc_id}: {key} missing at {p}", tool=tool, document_id=doc_id))

        try:
            standardized = StandardizedDocument.model_validate_json(Path(paths["standardized"]).read_text(encoding="utf-8"))
            if standardized.document_id != doc_id or standardized.tool.tool_name != tool:
                issues.append(Issue("error", "standardized_identity_mismatch", f"{tool}/{doc_id}: standardized identity mismatch", tool=tool, document_id=doc_id))
            if not standardized.pages:
                issues.append(Issue("error", "empty_standardized_document", f"{tool}/{doc_id}: no pages", tool=tool, document_id=doc_id))
        except Exception as exc:
            issues.append(Issue("error", "invalid_standardized_json", f"{tool}/{doc_id}: {exc}", tool=tool, document_id=doc_id))

        try:
            objs = read_json(Path(paths["object_evaluation"]))
            expected_ids = {x["object_id"] for x in expected_objects}
            actual_ids = {str(x.get("object_id")) for x in objs}
            if actual_ids != expected_ids:
                issues.append(Issue("error", "object_id_set_mismatch", f"{tool}/{doc_id}: expected={sorted(expected_ids)}, actual={sorted(actual_ids)}", tool=tool, document_id=doc_id))
            for obj in objs:
                score = obj.get("object_score")
                if not finite_number(score) or not 0 <= float(score) <= 100:
                    issues.append(Issue("error", "impossible_object_score", f"{tool}/{doc_id}/{obj.get('object_id')}: {score}", tool=tool, document_id=doc_id, value=score))
                for metric in obj.get("metrics") or []:
                    value = metric.get("metric_value")
                    if not finite_number(value) or not 0 <= float(value) <= 100:
                        issues.append(Issue("error", "impossible_metric_value", f"{tool}/{doc_id}/{obj.get('object_id')} {metric.get('metric_name')}: {value}", tool=tool, document_id=doc_id, metric=metric.get("metric_name"), value=value))
            total_objects += len(objs)
        except Exception as exc:
            issues.append(Issue("error", "invalid_object_evaluation", f"{tool}/{doc_id}: {exc}", tool=tool, document_id=doc_id))

        version = pair.get("adapter_version_snapshot") or {}
        if not (version.get("installed_version") or version.get("pinned_version")):
            issues.append(Issue("warning", "missing_version_snapshot", f"{tool}/{doc_id}: no adapter version", tool=tool, document_id=doc_id))

        raw_dir = Path(str(paths.get("raw_dir") or ""))
        if not raw_dir.exists() or not any(p.is_file() for p in raw_dir.rglob("*")):
            issues.append(Issue("error", "raw_output_missing", f"{tool}/{doc_id}: raw output is absent/empty", tool=tool, document_id=doc_id))

    # Validate category and Overall scores from evaluation report.
    report_path = run_dir / "metrics" / "evaluation_report.json"
    try:
        report = read_json(report_path)
        aggregate = report.get("aggregate_scores") or []
        tool_records = [x for x in aggregate if x.get("tool") == tool]
        scores = {x.get("score_name"): x.get("score") for x in tool_records}
        for category in CATEGORY_NAMES:
            name = f"{category}_score"
            value = scores.get(name)
            if value is None:
                issues.append(Issue("error", "missing_category_score", f"{tool}: {name} missing", tool=tool, metric=name))
            elif not finite_number(value) or not 0 <= float(value) <= 100:
                issues.append(Issue("error", "impossible_category_score", f"{tool}: {name}={value}", tool=tool, metric=name, value=value))
        overall = scores.get("overall_quality_score")
        if overall is None:
            issues.append(Issue("error", "missing_overall_score", f"{tool}: overall_quality_score missing", tool=tool))
        elif not finite_number(overall) or not 0 <= float(overall) <= 100:
            issues.append(Issue("error", "impossible_overall_score", f"{tool}: overall={overall}", tool=tool, value=overall))
    except Exception as exc:
        issues.append(Issue("error", "invalid_evaluation_report", f"{tool}: {exc}", tool=tool))

    if total_objects and total_objects != len(object_manifest):
        issues.append(Issue("error", "tool_total_object_count_mismatch", f"{tool}: {total_objects} object results, expected {len(object_manifest)}", tool=tool, value=total_objects))

    errors = sum(i.severity == "error" for i in issues)
    warnings = sum(i.severity == "warning" for i in issues)
    return {
        "tool": tool,
        "run_id": run_manifest.get("run_id"),
        "run_status": run_manifest.get("status"),
        "status": "clean" if errors == 0 else "corrupt",
        "object_result_count": total_objects,
        "error_count": errors,
        "warning_count": warnings,
        "issues": [i.as_dict() for i in issues],
    }


def _score_map(run_dir: Path, tool: str) -> dict[str, float | None]:
    report = read_json(run_dir / "metrics" / "evaluation_report.json")
    scores: dict[str, float | None] = {
        key: None
        for key in [
            *QUALITY_COLUMNS.values(),
            *CHEMISTRY_REPORTING_SCORES,
            "overall_score",
        ]
    }
    for rec in report.get("aggregate_scores") or []:
        if rec.get("tool") != tool:
            continue
        name = rec.get("score_name")
        if name in {f"{c}_score" for c in CATEGORY_NAMES}:
            scores[name] = float(rec["score"])
        elif name in CHEMISTRY_REPORTING_SCORES:
            scores[name] = float(rec["score"])
        elif name == "overall_quality_score":
            scores["overall_score"] = float(rec["score"])
    return scores


def _resource_pair_row(pair: dict[str, Any], pages: int) -> dict[str, Any]:
    raw = pair.get("adapter_raw_metadata") or {}
    usage = pair.get("adapter_resource_usage") or {}
    kind = pair.get("tool_kind")

    if kind == "cloud":
        processing = raw.get("processing_seconds")
        if not finite_number(processing) or float(processing) <= 0:
            processing = raw.get("latency_seconds")
        if not finite_number(processing) or float(processing) <= 0:
            processing = usage.get("wall_time_seconds")
    else:
        processing = usage.get("wall_time_seconds")
        if not finite_number(processing) or float(processing) <= 0:
            processing = pair.get("stage_timings_seconds", {}).get("adapter_current_invocation")

    processing = float(processing) if finite_number(processing) else None
    sec_page = (processing / pages) if processing is not None and pages > 0 else None

    ram = usage.get("peak_process_tree_rss_mb")
    if not finite_number(ram):
        ram = usage.get("peak_rss_mb")
    ram = float(ram) if finite_number(ram) else None

    # Prefer per-process GPU memory. If nvidia-smi cannot attribute memory
    # to the process, use device-wide used memory only when Torch telemetry
    # proves that this adapter actually used CUDA.
    gpu_process = usage.get("gpu_peak_process_mb")
    gpu_device = usage.get("gpu_peak_device_used_mb")
    torch_allocated = usage.get("torch_peak_allocated_mb")
    torch_reserved = usage.get("torch_peak_reserved_mb")

    if finite_number(gpu_process) and float(gpu_process) > 0:
        vram = float(gpu_process)
    else:
        torch_gpu_activity = any(
            finite_number(x) and float(x) > 0
            for x in (torch_allocated, torch_reserved)
        )
        if (
            torch_gpu_activity
            and finite_number(gpu_device)
            and float(gpu_device) > 0
        ):
            # Device-level upper bound, not process-exclusive usage.
            vram = float(gpu_device)
        else:
            vram = 0.0

    if kind == "local":
        cost = 0.0
        cost_basis = "not_applicable_local"
    else:
        actual = raw.get("actual_cost_usd")
        estimated = raw.get("estimated_cost_usd")
        if finite_number(actual):
            cost = float(actual)
            cost_basis = "actual"
        elif finite_number(estimated):
            cost = float(estimated)
            cost_basis = "estimated"
        else:
            cost = None
            cost_basis = "unavailable"

    return {
        "tool": pair.get("tool"),
        "document_id": pair.get("document_id"),
        "status": pair.get("status"),
        "pages": pages,
        "processing_seconds": processing,
        "sec_per_page": sec_page,
        "peak_ram_mb": ram,
        "peak_vram_mb": vram,
        "api_cost_usd": cost,
        "api_cost_basis": cost_basis,
        "matched_objects": pair.get("matched_count"),
        "missing_objects": pair.get("missing_count"),
        "adapter_action": pair.get("adapter_action"),
        "installed_version": (pair.get("adapter_version_snapshot") or {}).get("installed_version"),
        "pinned_version": (pair.get("adapter_version_snapshot") or {}).get("pinned_version"),
    }


def _iqr_bounds(values: list[float], multiplier: float) -> tuple[float, float] | None:
    if len(values) < 4:
        return None
    vals = sorted(values)

    def percentile(q: float) -> float:
        pos = (len(vals) - 1) * q
        lo, hi = math.floor(pos), math.ceil(pos)
        if lo == hi:
            return vals[lo]
        f = pos - lo
        return vals[lo] * (1 - f) + vals[hi] * f

    q1 = percentile(0.25)
    q3 = percentile(0.75)
    iqr = q3 - q1
    return q1 - multiplier * iqr, q3 + multiplier * iqr


def build_clean_results_dataset(
    project_root: Path,
    experiment_dir: Path,
    tool_runs: dict[str, str],
    integrity_reports: dict[str, dict[str, Any]],
    benchmark_cfg: BenchmarkConfig,
    experiment_cfg: dict[str, Any],
) -> dict[str, Any]:
    pages_by_doc = {k: int(v) for k, v in (experiment_cfg.get("expected_pages") or {}).items()}
    raw_tool_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    object_rows: list[dict[str, Any]] = []
    issues: list[Issue] = []

    for tool in benchmark_cfg.tools:
        run_id = tool_runs.get(tool)
        integrity = integrity_reports.get(tool, {})
        row: dict[str, Any] = {
            "tool": tool,
            "run_id": run_id,
            "integrity_status": integrity.get("status", "missing"),
            "errors_count": int(integrity.get("error_count", 0) or 0),
            "warnings_count": int(integrity.get("warning_count", 0) or 0),
        }
        if not run_id:
            issues.append(Issue("error", "missing_tool_run", f"No run for {tool}", tool=tool))
            raw_tool_rows.append(row)
            continue

        run_dir = project_root / benchmark_cfg.output_root / "runs" / run_id
        try:
            row.update(_score_map(run_dir, tool))
        except Exception as exc:
            issues.append(Issue("error", "score_read_failed", f"{tool}: {exc}", tool=tool))

        try:
            pairs = read_jsonl(run_dir / "pair_results.jsonl")
        except Exception as exc:
            pairs = []
            issues.append(Issue("error", "pair_read_failed", f"{tool}: {exc}", tool=tool))

        successful_pair_metrics = []
        for pair in pairs:
            doc_id = str(pair.get("document_id"))
            if pair.get("status") != "success":
                issues.append(Issue("error", "failed_run", f"{tool}/{doc_id}: {pair.get('error_message')}", tool=tool, document_id=doc_id))
                continue
            operational = _resource_pair_row(pair, pages_by_doc.get(doc_id, 0))
            pair_rows.append(operational)
            successful_pair_metrics.append(operational)

            obj_path = run_dir / "pairs" / tool / doc_id / "object_evaluation.json"
            try:
                for obj in read_json(obj_path):
                    object_rows.append({
                        "tool": tool,
                        "document_id": doc_id,
                        "page": obj.get("page"),
                        "object_id": obj.get("object_id"),
                        "object_type": obj.get("object_type"),
                        "category": obj.get("category"),
                        "subtype": obj.get("subtype"),
                        "matched": obj.get("matched"),
                        "object_score": obj.get("object_score"),
                    })
            except Exception as exc:
                issues.append(Issue("error", "object_dataset_read_failed", f"{tool}/{doc_id}: {exc}", tool=tool, document_id=doc_id))

        total_processing = sum(float(x["processing_seconds"]) for x in successful_pair_metrics if finite_number(x.get("processing_seconds")))
        total_pages = sum(int(x["pages"]) for x in successful_pair_metrics)
        row["total_pages"] = total_pages
        row["total_processing_time_sec"] = total_processing if successful_pair_metrics else None
        row["sec_per_page"] = total_processing / total_pages if total_pages > 0 else None

        ram_values = [float(x["peak_ram_mb"]) for x in successful_pair_metrics if finite_number(x.get("peak_ram_mb"))]
        row["peak_ram_mb"] = max(ram_values) if ram_values else None
        vram_values = [float(x["peak_vram_mb"]) for x in successful_pair_metrics if finite_number(x.get("peak_vram_mb"))]
        row["peak_vram_mb"] = max(vram_values) if vram_values else 0.0

        costs = [x for x in successful_pair_metrics if x.get("api_cost_basis") not in {"not_applicable_local"}]
        known_costs = [float(x["api_cost_usd"]) for x in costs if finite_number(x.get("api_cost_usd"))]
        if TOOL_SPECS[tool].kind == "local":
            row["api_cost_usd"] = 0.0
            row["api_cost_basis"] = "not_applicable_local"
        elif len(known_costs) == len(costs) and costs:
            row["api_cost_usd"] = sum(known_costs)
            bases = {x["api_cost_basis"] for x in costs}
            row["api_cost_basis"] = bases.pop() if len(bases) == 1 else "mixed"
        else:
            row["api_cost_usd"] = None
            row["api_cost_basis"] = "unavailable_or_partial"

        row["failed_runs_count"] = sum(1 for p in pairs if p.get("status") != "success")
        row["matched_objects"] = sum(int(p.get("matched_count", 0) or 0) for p in pairs if p.get("status") == "success")
        row["missing_objects"] = sum(int(p.get("missing_count", 0) or 0) for p in pairs if p.get("status") == "success")
        raw_tool_rows.append(row)

    # Impossible/missing values: hard quality-control issues.
    score_cols = [*QUALITY_COLUMNS.values(), "overall_score"]
    for row in raw_tool_rows:
        tool = row["tool"]
        for col in score_cols:
            value = row.get(col)
            if value is None:
                issues.append(Issue("error", "missing_required_metric", f"{tool}: {col} missing", tool=tool, metric=col))
            elif not finite_number(value) or not 0 <= float(value) <= 100:
                issues.append(Issue("error", "impossible_score", f"{tool}: {col}={value}", tool=tool, metric=col, value=value))
        for col in CHEMISTRY_REPORTING_SCORES:
            value = row.get(col)
            if value is not None and (
                not finite_number(value) or not 0 <= float(value) <= 100
            ):
                issues.append(
                    Issue(
                        "error",
                        "impossible_optional_score",
                        f"{tool}: {col}={value}",
                        tool=tool,
                        metric=col,
                        value=value,
                    )
                )
        for col in ["sec_per_page", "total_processing_time_sec", "peak_ram_mb", "peak_vram_mb", "api_cost_usd"]:
            value = row.get(col)
            if value is None:
                if col == "api_cost_usd" and TOOL_SPECS[tool].kind == "cloud":
                    issues.append(Issue("warning", "cost_unavailable", f"{tool}: API cost unavailable", tool=tool, metric=col))
                else:
                    issues.append(Issue("error", "missing_operational_metric", f"{tool}: {col} missing", tool=tool, metric=col))
            elif not finite_number(value) or float(value) < 0:
                issues.append(Issue("error", "impossible_operational_value", f"{tool}: {col}={value}", tool=tool, metric=col, value=value))
        expected_pages = int(experiment_cfg.get("expected_total_pages", 0) or 0)
        if expected_pages and row.get("total_pages") != expected_pages:
            issues.append(Issue("error", "incomplete_page_total", f"{tool}: total_pages={row.get('total_pages')}, expected={expected_pages}", tool=tool, value=row.get("total_pages")))
        if row.get("failed_runs_count", 0):
            issues.append(Issue("error", "failed_runs_present", f"{tool}: {row['failed_runs_count']} failed document runs", tool=tool, value=row["failed_runs_count"]))
        if row.get("integrity_status") != "clean":
            issues.append(Issue("error", "tool_integrity_failed", f"{tool}: integrity_status={row.get('integrity_status')}", tool=tool))

    # Statistical outliers are warnings only, never silently removed.
    multiplier = float(experiment_cfg.get("outlier_iqr_multiplier", 1.5))
    outlier_columns = [*score_cols, "sec_per_page", "total_processing_time_sec", "peak_ram_mb", "peak_vram_mb", "api_cost_usd"]
    for col in outlier_columns:
        values = [float(r[col]) for r in raw_tool_rows if finite_number(r.get(col))]
        bounds = _iqr_bounds(values, multiplier)
        if bounds is None:
            continue
        low, high = bounds
        for row in raw_tool_rows:
            value = row.get(col)
            if finite_number(value) and (float(value) < low or float(value) > high):
                issues.append(Issue("warning", "statistical_outlier_iqr", f"{row['tool']}: {col}={value} outside [{low:.4g}, {high:.4g}]", tool=row["tool"], metric=col, value=value))

    hard_bad_tools = {i.tool for i in issues if i.severity == "error" and i.tool}
    clean_rows = [r for r in raw_tool_rows if r["tool"] not in hard_bad_tools]

    result_dir = ensure_dir(experiment_dir / "results")
    write_csv(result_dir / "raw_results_dataset.csv", raw_tool_rows)
    write_csv(result_dir / "clean_results_dataset.csv", clean_rows)
    write_csv(result_dir / "pair_operational_dataset.csv", pair_rows)
    write_csv(result_dir / "object_results_dataset.csv", object_rows)
    write_csv(result_dir / "quality_issues.csv", [i.as_dict() for i in issues])

    summary = {
        "created_at": utc_now(),
        "raw_tool_rows": len(raw_tool_rows),
        "clean_tool_rows": len(clean_rows),
        "pair_rows": len(pair_rows),
        "object_rows": len(object_rows),
        "error_count": sum(i.severity == "error" for i in issues),
        "warning_count": sum(i.severity == "warning" for i in issues),
        "clean_tools": [r["tool"] for r in clean_rows],
        "excluded_tools": sorted(hard_bad_tools),
        "status": "clean" if len(clean_rows) == len(benchmark_cfg.tools) and not any(i.severity == "error" for i in issues) else "needs_attention",
    }
    write_json(result_dir / "data_quality_summary.json", summary)
    return summary


def package_experiment(experiment_dir: Path) -> Path:
    target = experiment_dir.parent / f"{experiment_dir.name}.zip"
    if target.exists():
        target.unlink()
    shutil.make_archive(str(target.with_suffix("")), "zip", root_dir=experiment_dir)
    return target


class FullExperimentRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        benchmark_config_path: Path,
        experiment_config_path: Path,
        seed: int | None = None,
        force_api: bool = False,
        force_rerun: bool = False,
        reuse_stale_raw: bool = False,
        stop_after_corrupt_tool: bool | None = None,
    ):
        self.project_root = project_root.resolve()
        self.benchmark_config_path = benchmark_config_path
        self.experiment_config_path = experiment_config_path
        self.benchmark_cfg = BenchmarkConfig.from_yaml(benchmark_config_path)
        self.experiment_cfg = _experiment_config(experiment_config_path)
        self.seed = int(seed if seed is not None else self.benchmark_cfg.seed)
        self.force_api = force_api
        self.force_rerun = force_rerun
        self.reuse_stale_raw = reuse_stale_raw
        self.stop_after_corrupt_tool = bool(
            self.experiment_cfg.get("stop_after_corrupt_tool", False)
            if stop_after_corrupt_tool is None
            else stop_after_corrupt_tool
        )

    def run(self, experiment_id: str | None = None) -> dict[str, Any]:
        experiment_id = experiment_id or make_experiment_id(self.seed)
        experiment_dir = ensure_dir(
            self.project_root / self.benchmark_cfg.output_root / "experiments" / experiment_id
        )
        preflight = run_preflight(
            self.project_root,
            self.benchmark_config_path,
            self.experiment_config_path,
        )
        write_json(experiment_dir / "preflight_report.json", preflight)
        if preflight["status"] != "pass":
            manifest = {
                "experiment_id": experiment_id,
                "status": "preflight_failed",
                "started_at": utc_now(),
                "finished_at": utc_now(),
                "preflight_error_count": preflight["error_count"],
                "tool_runs": {},
            }
            write_json(experiment_dir / "experiment_manifest.json", manifest)
            bundle = package_experiment(experiment_dir)
            manifest["bundle"] = str(bundle)
            write_json(experiment_dir / "experiment_manifest.json", manifest)
            return manifest

        started = utc_now()
        tool_runs: dict[str, str] = {}
        integrity_reports: dict[str, dict[str, Any]] = {}
        documents = [d.document_id for d in self.benchmark_cfg.documents]

        for tool in self.benchmark_cfg.tools:
            tool_run_id = f"exp_{experiment_id}_{tool}"
            runner = BenchmarkRunner(
                project_root=self.project_root,
                config=self.benchmark_cfg,
                seed=self.seed,
                force_rerun=self.force_rerun,
                force_api=self.force_api,
                reuse_stale_raw=self.reuse_stale_raw,
                fail_fast=False,
                mock_cloud=False,
            )
            try:
                run_manifest = runner.run(
                    tools=[tool],
                    documents=documents,
                    run_id=tool_run_id,
                )
                tool_runs[tool] = run_manifest["run_id"]
                run_dir = self.project_root / self.benchmark_cfg.output_root / "runs" / run_manifest["run_id"]
                integrity = validate_tool_run(self.project_root, run_dir, tool, self.benchmark_cfg)
            except Exception as exc:
                integrity = {
                    "tool": tool,
                    "run_id": tool_run_id,
                    "status": "corrupt",
                    "error_count": 1,
                    "warning_count": 0,
                    "issues": [Issue("error", "tool_execution_exception", f"{type(exc).__name__}: {exc}", tool=tool).as_dict()],
                }
                # If the runner managed to create its run folder, retain the ID.
                if (self.project_root / self.benchmark_cfg.output_root / "runs" / tool_run_id).exists():
                    tool_runs[tool] = tool_run_id

            integrity_reports[tool] = integrity
            write_json(experiment_dir / "integrity" / f"{tool}.json", integrity)
            if integrity.get("status") != "clean" and self.stop_after_corrupt_tool:
                break

        summary = build_clean_results_dataset(
            self.project_root,
            experiment_dir,
            tool_runs,
            integrity_reports,
            self.benchmark_cfg,
            self.experiment_cfg,
        )

        manifest = {
            "schema_version": "1.0",
            "pipeline_version": "prompt12-v1",
            "experiment_id": experiment_id,
            "status": "clean" if summary["status"] == "clean" else "needs_attention",
            "started_at": started,
            "finished_at": utc_now(),
            "seed": self.seed,
            "tool_runs": tool_runs,
            "integrity": {tool: rep.get("status") for tool, rep in integrity_reports.items()},
            "data_quality_summary": summary,
            "preflight_report": "preflight_report.json",
            "results_dir": "results",
        }
        write_json(experiment_dir / "experiment_manifest.json", manifest)
        bundle = package_experiment(experiment_dir)
        manifest["bundle"] = str(bundle)
        write_json(experiment_dir / "experiment_manifest.json", manifest)
        return manifest
