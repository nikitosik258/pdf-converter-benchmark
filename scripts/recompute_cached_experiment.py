"""Recompute matching/evaluation from a completed experiment's normalized cache.

No adapter execution, environment/credential probes, or cache writes. Historical
adapter timing/resource/cost metadata is retained; new artifacts get new IDs.
"""
from __future__ import annotations

import argparse
import copy
import json
import platform
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf_benchmark.benchmark.cache import sha256_file
from pdf_benchmark.benchmark.config import BenchmarkConfig
from pdf_benchmark.benchmark.experiment import (
    build_clean_results_dataset, make_experiment_id, package_experiment,
    read_jsonl, utc_now, validate_ground_truth_content, validate_tool_run, write_jsonl,
)
from pdf_benchmark.benchmark.ground_truth import (
    load_ground_truth, load_object_manifest, validate_ground_truth_manifest,
)
from pdf_benchmark.benchmark.matching import MatchingConfig, match_document
from pdf_benchmark.benchmark.results import build_run_report
from pdf_benchmark.benchmark.runner import _pair_object_results
from pdf_benchmark.evaluation.framework import EvaluationFramework
from pdf_benchmark.evaluation.scoring import ScoringConfig
from pdf_benchmark.evaluation.storage import save_evaluation_report
from pdf_benchmark.models import StandardizedDocument
from pdf_benchmark.normalization.pipeline import NormalizationConfig
from pdf_benchmark.utils.io import read_json, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-experiment", required=True)
    parser.add_argument("--experiment-id")
    args = parser.parse_args()
    cfg = BenchmarkConfig.from_yaml(ROOT / "config/benchmark.yaml")
    scoring = ScoringConfig.from_yaml(ROOT / cfg.metrics_config)
    normalization = NormalizationConfig.from_yaml(ROOT / cfg.normalization_config)
    matching = MatchingConfig.model_validate(read_yaml(ROOT / cfg.matching_config)["matching"])
    exp_cfg = read_yaml(ROOT / "config/experiment.yaml")["experiment"]
    new_id = args.experiment_id or make_experiment_id(cfg.seed)
    for name in (args.source_experiment, new_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Experiment IDs must contain only letters, numbers, '-' and '_'")
    bench = ROOT / cfg.output_root
    source = bench / "experiments" / args.source_experiment
    target = bench / "experiments" / new_id
    new_runs = {t: f"exp_{new_id}_{t}" for t in cfg.tools}
    if target.exists() or (target.parent / f"{new_id}.zip").exists() or any((bench / "runs" / run).exists() for run in new_runs.values()):
        raise FileExistsError("Target experiment/run already exists; historical artifacts cannot be overwritten")

    source_manifest = read_json(source / "experiment_manifest.json")
    preflight = read_json(source / "preflight_report.json")
    if source_manifest["status"] != "clean" or set(source_manifest["tool_runs"]) != set(cfg.tools):
        raise ValueError("Source experiment must be clean and cover the active roster")
    gt = load_ground_truth(ROOT / cfg.ground_truth_dir)
    validate_ground_truth_manifest(gt, load_object_manifest(ROOT / cfg.object_manifest))
    gt_issues = validate_ground_truth_content(gt, require_bbox_all=True)
    if any(i.severity == "error" for i in gt_issues):
        raise ValueError("Ground Truth validation failed")
    for relative, expected in preflight["ground_truth_hashes"].items():
        if sha256_file(ROOT / relative) != expected:
            raise ValueError(f"Ground Truth differs from source experiment: {relative}")
    for pdf in preflight["pdfs"]:
        if sha256_file(Path(pdf["path"])) != pdf["sha256"]:
            raise ValueError(f"PDF differs from source experiment: {pdf['document_id']}")

    protected = set(p for p in source.rglob("*") if p.is_file())
    source_zip = source.parent / f"{args.source_experiment}.zip"
    if source_zip.exists():
        protected.add(source_zip)
    protected.update(ROOT / p for p in preflight["ground_truth_hashes"])
    protected.update(Path(p["path"]) for p in preflight["pdfs"])
    protected.update(p for p in (ROOT / "config").rglob("*.yaml"))
    original_runs, original_pairs = {}, {}
    for tool, run_id in source_manifest["tool_runs"].items():
        run_dir = bench / "runs" / run_id
        original_runs[tool] = read_json(run_dir / "run_manifest.json")
        if original_runs[tool]["benchmark_config"] != cfg.model_dump(mode="json"):
            raise ValueError("Benchmark configuration differs from source experiment")
        protected.update(p for p in run_dir.rglob("*") if p.is_file())
        pairs = read_jsonl(run_dir / "pair_results.jsonl")
        if len(pairs) != len(cfg.documents) or {p["document_id"] for p in pairs} != set(cfg.document_map()):
            raise ValueError(f"Source pair grid is incomplete: {tool}")
        for pair in pairs:
            if pair["status"] != "success" or pair["tool"] != tool:
                raise ValueError("Source contains a failed or misidentified pair")
            cache = Path(pair["cache_dir"])
            protected.update(p for p in cache.rglob("*") if p.is_file())
            document = StandardizedDocument.model_validate_json(Path(pair["paths"]["normalized"]).read_text(encoding="utf-8"))
            if document.document_id != pair["document_id"] or document.tool.tool_name != tool:
                raise ValueError("Normalized cache identity mismatch")
            if document.metadata.get("normalization", {}).get("config_sha256") != normalization.fingerprint():
                raise ValueError("Normalized cache is incompatible with current normalization config")
        original_pairs[tool] = pairs
    print(f"Hashing {len(protected)} protected input files", flush=True)
    hashes = {str(p.relative_to(ROOT)): sha256_file(p) for p in sorted(protected)}

    target.mkdir(parents=True)
    started = utc_now()
    write_json(target / "source_input_hashes.json", hashes)
    write_json(target / "code_sha256.json", {
        str(p.relative_to(ROOT)): sha256_file(p)
        for p in [*sorted((ROOT / "src").rglob("*.py")), Path(__file__)]
    })
    write_json(target / "preflight_report.json", {
        "created_at": started, "status": "pass", "mode": "cached_normalized_only",
        "source_preflight": str(source / "preflight_report.json"),
        "pdfs": preflight["pdfs"], "ground_truth_hashes": preflight["ground_truth_hashes"],
        "ground_truth_object_count": len(gt), "total_pages": sum(p["pages"] for p in preflight["pdfs"]),
        "adapter_probes_performed": False, "credential_probes_performed": False,
        "issues": [i.as_dict() for i in gt_issues], "error_count": 0,
    })
    integrity = {}
    for tool in cfg.tools:
        run_id = new_runs[tool]
        run_dir = bench / "runs" / run_id
        run_started = utc_now()
        objects, pairs = [], []
        for original in original_pairs[tool]:
            doc_id = original["document_id"]
            pair_dir = run_dir / "pairs" / tool / doc_id
            document = StandardizedDocument.model_validate_json(Path(original["paths"]["normalized"]).read_text(encoding="utf-8"))
            t0 = time.perf_counter()
            matches = match_document([g for g in gt if g.document_id == doc_id], document, config=matching)
            t1 = time.perf_counter()
            evaluated = _pair_object_results(tool=tool, document_id=doc_id, matches=matches, framework=EvaluationFramework(scoring))
            t2 = time.perf_counter()
            write_json(pair_dir / "matches.json", [m.model_dump(mode="json") for m in matches])
            write_json(pair_dir / "object_evaluation.json", [o.model_dump(mode="json") for o in evaluated])
            pair = copy.deepcopy(original)
            pair.update({
                "adapter_action": "reuse_normalized", "resume_notes": ["downstream_recompute_from_source_experiment"],
                "source_experiment_id": args.source_experiment,
                "stage_timings_seconds": {"adapter_current_invocation": 0.0, "normalization": 0.0,
                    "matching": t1-t0, "evaluation": t2-t1, "pair_total_current_invocation": t2-t0},
                "downstream_resource_usage": {}, "downstream_resource_note": "not measured during cached recomputation",
                "object_count": len(evaluated), "matched_count": sum(m.matched for m in matches),
                "missing_count": sum(not m.matched for m in matches),
            })
            pair["paths"].update({"matches": str(pair_dir / "matches.json"), "object_evaluation": str(pair_dir / "object_evaluation.json")})
            write_json(pair_dir / "pair_result.json", pair)
            objects.extend(evaluated)
            pairs.append(pair)
        report = build_run_report(objects, scoring_config=scoring)
        report.metadata.update({"run_id": run_id, "source_experiment_id": args.source_experiment,
            "selected_tools": [tool], "selected_documents": list(cfg.document_map()), "failures": [], "seed": cfg.seed})
        metric_paths = save_evaluation_report(report, run_dir / "metrics")
        write_jsonl(run_dir / "pair_results.jsonl", pairs)
        run = copy.deepcopy(original_runs[tool])
        run.update({"run_id": run_id, "pipeline_version": "cached-evaluation-v1", "started_at": run_started,
            "finished_at": utc_now(), "metric_paths": metric_paths, "source_experiment_id": args.source_experiment,
            "mode": "cached_normalized_only", "git_commit": None,
            "python_version": platform.python_version(), "platform": platform.platform()})
        write_json(run_dir / "run_manifest.json", run)
        integrity[tool] = validate_tool_run(ROOT, run_dir, tool, cfg)
        write_json(target / "integrity" / f"{tool}.json", integrity[tool])
        if integrity[tool]["status"] != "clean":
            raise RuntimeError(f"Integrity check failed: {tool}")
        print(f"{tool}: {len(objects)} objects, integrity=clean", flush=True)

    summary = build_clean_results_dataset(ROOT, target, new_runs, integrity, cfg, exp_cfg)
    changes = [p for p, h in hashes.items() if sha256_file(ROOT / p) != h]
    preservation = {"checked_files": len(hashes), "changed_files": changes, "status": "pass" if not changes else "fail"}
    write_json(target / "source_preservation.json", preservation)
    manifest = {
        "schema_version": "1.0", "pipeline_version": "prompt12-cached-text-dedup-v1",
        "experiment_id": new_id, "source_experiment_id": args.source_experiment,
        "status": "clean" if summary["status"] == "clean" and not changes else "needs_attention",
        "started_at": started, "finished_at": utc_now(), "seed": cfg.seed, "tool_runs": new_runs,
        "integrity": {t: r["status"] for t, r in integrity.items()}, "data_quality_summary": summary,
        "preflight_report": "preflight_report.json", "results_dir": "results",
        "mode": "cached_normalized_only", "adapter_executions": 0, "cloud_calls": 0,
        "methodological_change": "Text fallback deduplicates equal normalized text only at equal bbox; repeated words at different positions are preserved.",
        "adapter_operational_metrics": "inherited unchanged from source experiment",
        "source_preservation": preservation, "bundle": str(target.parent / f"{new_id}.zip"),
    }
    write_json(target / "experiment_manifest.json", manifest)
    package_experiment(target)
    print(json.dumps({"experiment_id": new_id, **summary, "source_preservation": preservation}, indent=2))
    if manifest["status"] != "clean":
        raise SystemExit(1)


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
