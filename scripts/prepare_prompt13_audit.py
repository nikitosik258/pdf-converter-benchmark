"""Read-only audit of a completed experiment before Prompt 13.

Writes preparation artifacts only. Never runs adapters, contacts vendors,
changes GT/caches/results, or applies the proposed matcher patch.
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf_benchmark.benchmark import matching
from pdf_benchmark.benchmark.ground_truth import (
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)
from pdf_benchmark.evaluation.aggregation import aggregate_tool_scores
from pdf_benchmark.evaluation.models import ObjectEvaluationResult
from pdf_benchmark.evaluation.scoring import CATEGORY_ORDER, ScoringConfig
from pdf_benchmark.evaluation.text_metrics import calculate_text_metrics
from pdf_benchmark.models import BBox, Page, StandardizedDocument, TextBlock

EXPERIMENT = "20260930T193224Z_s42_704070d5"
BENCH = ROOT / "outputs/benchmark"
EXP = BENCH / "experiments" / EXPERIMENT
OUT = ROOT / "outputs/statistical_analysis_preparation" / EXPERIMENT
LOCAL = {"pymupdf", "pdfplumber", "pdfminer", "docling", "mineru"}
CLOUD = {"ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse"}
DOCS = {"D01": 43, "D02": 7, "D03": 14, "D04": 12, "D05": 22}
EXPECTED_CHEMISTRY = {
    "pymupdf": (82.432814478183, 74.904020683119),
    "pdfplumber": (72.587548791529, 60.839355416469),
    "docling": (73.448501590386, 62.069287986265),
    "pdfminer": (82.587548791528, 75.125069702183),
    "mineru": (47.395047906711, 39.035989659795),
    "ocr_space": (64.057141039719, 61.690462103072),
    "nutrient": (70.381657680873, 57.688082401247),
    "mindee": (52.332710188652, 44.003775172977),
    "adobe_extract": (69.903848066315, 57.005497237593),
    "llamaparse": (69.804140207212, 56.863057438874),
}
TYPE_CATEGORY = {
    "text": "text", "table": "table", "math_formula": "math",
    "chemical_formula": "chemistry", "chemical_structure": "chemistry",
    "image": "image", "diagram": "diagram",
}
HASHES: dict[str, str] = {}
CHECKS: list[dict] = []


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def track(path: Path) -> Path:
    HASHES.setdefault(path.relative_to(ROOT).as_posix(), sha(path))
    return path


def read_json(path: Path):
    return json.loads(track(path).read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(s) for s in track(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def read_csv(path: Path) -> list[dict]:
    with track(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(name: str, payload) -> None:
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(name: str, rows: list[dict]) -> None:
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def check(name: str, ok: bool, detail=None) -> None:
    CHECKS.append({"check": name, "passed": bool(ok), "detail": detail})


def close(a, b) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-8)


def object_key(row: dict) -> tuple[str, str, str]:
    return row["tool"], row["document_id"], row["object_id"]


def category_mean(rows: list[dict], category: str) -> float:
    if category == "chemistry":
        subgroups = collections.defaultdict(list)
        for row in rows:
            subgroups[row["subtype"]].append(float(row["object_score"]))
        return mean(mean(scores) for scores in subgroups.values())
    return mean(float(row["object_score"]) for row in rows)


def proposed_matcher():
    """Recognize the applied repair or diagnose the original matcher in memory."""
    path = ROOT / "src/pdf_benchmark/benchmark/matching.py"
    original = track(path).read_text(encoding="utf-8")
    old = """    # Remove exact duplicate text blocks while preserving reading order.
    ordered: list[tuple[Candidate, float]] = []
    seen_text: set[str] = set()
    for candidate, overlap in selected:
        normalized = _pred_text(candidate)
        if not normalized:
            continue
        if normalized in seen_text:
            continue
        seen_text.add(normalized)
        ordered.append((candidate, overlap))
"""
    new = """    # Only identical text at identical coordinates is a duplicate.
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
"""
    if original.count(new) == 1:
        return {**vars(matching), "_dedup_fixed": True}
    if original.count(old) != 1:
        raise RuntimeError("Matcher source differs from the reviewed baseline; review the proposed patch again")
    proposed = original.replace(old, new)
    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), proposed.splitlines(keepends=True),
        fromfile="a/src/pdf_benchmark/benchmark/matching.py",
        tofile="b/src/pdf_benchmark/benchmark/matching.py",
    ))
    (OUT / "proposed_text_dedup.patch").write_text(patch, encoding="utf-8")
    namespace = dict(vars(matching))
    exec(compile(proposed, str(path), "exec"), namespace)
    namespace["_dedup_fixed"] = False
    return namespace


def main() -> int:
    global EXPERIMENT, EXP, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id")
    args = parser.parse_args()
    latest = BENCH / "latest_experiment.txt"
    experiment_id = args.experiment_id or (latest.read_text(encoding="utf-8").strip() if latest.exists() else EXPERIMENT)
    if not experiment_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in experiment_id):
        raise ValueError("Invalid experiment ID")
    EXPERIMENT = experiment_id
    EXP = BENCH / "experiments" / EXPERIMENT
    OUT = ROOT / "outputs/statistical_analysis_preparation" / EXPERIMENT
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = read_json(EXP / "experiment_manifest.json")
    preflight = read_json(EXP / "preflight_report.json")
    quality = read_json(EXP / "results/data_quality_summary.json")
    tool_rows = read_csv(EXP / "results/clean_results_dataset.csv")
    raw_rows = read_csv(EXP / "results/raw_results_dataset.csv")
    pair_rows = read_csv(EXP / "results/pair_operational_dataset.csv")
    object_rows = read_csv(EXP / "results/object_results_dataset.csv")
    issues = read_csv(EXP / "results/quality_issues.csv")
    check("row_counts", (len(tool_rows), len(pair_rows), len(object_rows)) == (10, 50, 400))
    check("active_roster", {r["tool"] for r in tool_rows} == LOCAL | CLOUD == set(manifest["tool_runs"]))
    check("raw_equals_clean", raw_rows == tool_rows)
    check(
        "saved_quality_summary",
        quality["status"] == "clean"
        and quality["error_count"] == 0
        and quality["warning_count"] == len(issues),
    )
    check(
        "iqr_warnings_retained",
        bool(issues)
        and all(
            r["severity"] == "warning"
            and r["code"] == "statistical_outlier_iqr"
            for r in issues
        ),
        {"warning_count": len(issues)},
    )
    pair_map = {(r["tool"], r["document_id"]): r for r in pair_rows}
    object_map = {object_key(r): r for r in object_rows}
    check("unique_pair_keys", len(pair_map) == len(pair_rows) == 50)
    check("unique_object_keys", len(object_map) == len(object_rows) == 400)
    check("complete_pair_grid", set(pair_map) == {(t, d) for t in LOCAL | CLOUD for d in DOCS})

    for path in sorted((ROOT / "src").rglob("*.py")):
        track(path)
    for path in sorted((ROOT / "config").rglob("*.yaml")):
        track(path)
    for path in (ROOT / "pyproject.toml", Path(__file__)):
        track(path)
    for path, expected in preflight["ground_truth_hashes"].items():
        current = ROOT / path
        check("gt_hash:" + path, sha(track(current)) == expected)
    for doc in preflight["pdfs"]:
        path = ROOT / "documents" / doc["filename"]
        check("pdf_hash:" + doc["document_id"], sha(track(path)) == doc["sha256"] and path.stat().st_size == doc["size_bytes"])

    gt = load_ground_truth(ROOT / "ground_truth")
    validate_ground_truth_manifest(gt, load_object_manifest(ROOT / "benchmark/object_manifest.json"))
    check("gt_manifest_identity", len(gt) == 40)
    gt_map = {g.object_id: g for g in gt}
    coverage = collections.Counter((g.document_id, TYPE_CATEGORY[g.object_type]) for g in gt)
    coverage_rows = [{"document_id": d, **{c: coverage[d, c] for c in CATEGORY_ORDER}} for d in DOCS]
    write_csv("ground_truth_coverage.csv", coverage_rows)
    check("object_grid_matches_gt", set(object_map) == {(t, g.document_id, g.object_id) for t in LOCAL | CLOUD for g in gt})
    check("object_identity_and_ranges", all(
        r["document_id"] == gt_map[r["object_id"]].document_id
        and int(r["page"]) == gt_map[r["object_id"]].page
        and r["object_type"] == gt_map[r["object_id"]].object_type
        and r["category"] == TYPE_CATEGORY[r["object_type"]]
        and math.isfinite(float(r["object_score"])) and 0 <= float(r["object_score"]) <= 100
        and r["matched"] in {"True", "False"}
        and (r["matched"] == "True" or float(r["object_score"]) == 0)
        for r in object_rows
    ))

    cfg = ScoringConfig.from_yaml(ROOT / "config/metrics.yaml")
    candidate = proposed_matcher()
    diagnostics, operational, uncertainty, document_scores = [], [], [], []
    match_counts = collections.defaultdict(collections.Counter)
    all_chem_count = 0
    visual_contexts = []
    max_score_difference = 0.0
    for tool_row in tool_rows:
        tool = tool_row["tool"]
        run_dir = BENCH / "runs" / manifest["tool_runs"][tool]
        report = read_json(run_dir / "metrics/evaluation_report.json")
        run = read_json(run_dir / "run_manifest.json")
        integrity = read_json(EXP / "integrity" / f"{tool}.json")
        check(tool + ":saved_integrity", integrity["status"] == "clean" and run["status"] == "success")
        saved_pairs = read_jsonl(run_dir / "pair_results.jsonl")
        source_objects = report["object_results"]
        check(tool + ":report_objects_match_csv", len(source_objects) == 40 and all(
            object_key(o) in object_map and close(o["object_score"], object_map[object_key(o)]["object_score"])
            and str(o["matched"]) == object_map[object_key(o)]["matched"] for o in source_objects
        ))
        reaggregated = aggregate_tool_scores([ObjectEvaluationResult.model_validate(o) for o in source_objects], config=cfg)
        stored = {r["score_name"]: r for r in report["aggregate_scores"] if r["scope"] in {"category", "tool"}}
        check(tool + ":reproduce_saved_scores_and_ci", all(
            close(r.score, stored[r.score_name]["score"])
            and (r.ci95_low is None or (close(r.ci95_low, stored[r.score_name]["ci95_low"]) and close(r.ci95_high, stored[r.score_name]["ci95_high"])))
            for r in reaggregated
        ))
        for record in report["aggregate_scores"]:
            if record["scope"] == "document" and record["category"]:
                document_scores.append({k: record[k] for k in ("tool", "document_id", "category", "score", "n_objects")})
            if (
                record["scope"] == "category"
                and record["score_name"] == f"{record['category']}_score"
            ):
                uncertainty.append({k: record[k] for k in ("tool", "category", "score", "n_objects", "n_documents", "std_dev", "ci95_low", "ci95_high", "ci_basis")})
        independent = {}
        for category in CATEGORY_ORDER:
            doc_means = [category_mean([r for r in object_rows if r["tool"] == tool and r["document_id"] == d and r["category"] == category], category)
                         for d in DOCS if coverage[d, category]]
            independent[category] = mean(doc_means)
            delta = abs(independent[category] - float(tool_row[category + "_score"]))
            max_score_difference = max(max_score_difference, delta)
            check(tool + ":independent_macro:" + category, close(independent[category], tool_row[category + "_score"]))
        check(tool + ":overall_six_equal_categories", close(mean(independent.values()), tool_row["overall_score"]))
        op = [r for r in pair_rows if r["tool"] == tool]
        check(tool + ":operational_totals", int(tool_row["total_pages"]) == sum(int(p["pages"]) for p in op) == 98
              and close(tool_row["total_processing_time_sec"], sum(float(p["processing_seconds"]) for p in op))
              and close(tool_row["sec_per_page"], float(tool_row["total_processing_time_sec"]) / 98)
              and close(tool_row["peak_ram_mb"], max(float(p["peak_ram_mb"]) for p in op))
              and close(tool_row["peak_vram_mb"], max(float(p["peak_vram_mb"]) for p in op))
              and close(tool_row["api_cost_usd"], sum(float(p["api_cost_usd"]) for p in op))
              and int(tool_row["matched_objects"]) + int(tool_row["missing_objects"]) == 40
              and int(tool_row["failed_runs_count"]) == int(tool_row["errors_count"]) == 0)
        for pair in saved_pairs:
            doc = pair["document_id"]
            label = tool + "/" + doc
            op_row = pair_map[tool, doc]
            check(label + ":pair_integrity", pair["status"] == op_row["status"] == "success"
                  and int(op_row["pages"]) == DOCS[doc]
                  and pair["object_count"] == sum(coverage[doc, c] for c in CATEGORY_ORDER)
                  and pair["adapter_action"] in {"reuse_standardized", "reuse_normalized", "standardize_only"})
            check(label + ":operational_ranges", all(math.isfinite(float(op_row[k])) and float(op_row[k]) >= 0 for k in ("processing_seconds", "sec_per_page", "peak_ram_mb", "peak_vram_mb", "api_cost_usd"))
                  and close(op_row["sec_per_page"], float(op_row["processing_seconds"]) / DOCS[doc]))
            raw, usage = pair["adapter_raw_metadata"], pair["adapter_resource_usage"]
            timing_field = next((k for k in ("processing_seconds", "latency_seconds") if isinstance(raw.get(k), (int, float)) and raw[k] > 0), None) if tool in CLOUD else None
            elapsed = raw[timing_field] if timing_field else usage["wall_time_seconds"]
            gpu_process = usage.get("gpu_peak_process_mb") or 0
            torch_active = (usage.get("torch_peak_allocated_mb") or 0) > 0 or (usage.get("torch_peak_reserved_mb") or 0) > 0
            expected_gpu = gpu_process if gpu_process > 0 else (usage.get("gpu_peak_device_used_mb") or 0) if torch_active else 0
            expected_ram = usage.get("peak_process_tree_rss_mb", usage.get("peak_rss_mb"))
            cost_field = "actual_cost_usd" if raw.get("actual_cost_usd") is not None else "estimated_cost_usd"
            expected_cost = raw.get(cost_field) if tool in CLOUD else 0
            check(label + ":operational_provenance", close(op_row["processing_seconds"], elapsed) and close(op_row["peak_vram_mb"], expected_gpu)
                  and close(op_row["peak_ram_mb"], expected_ram) and close(op_row["api_cost_usd"], expected_cost))
            operational.append({"tool": tool, "document_id": doc, "kind": pair["tool_kind"],
                "timing_basis": "raw." + timing_field if timing_field else "adapter_wall_time_seconds",
                "vram_basis": "process" if gpu_process > 0 else "device_upper_bound_with_torch_activity" if torch_active and expected_gpu else "no_attributable_gpu",
                "ram_scope": "cloud_client" if tool in CLOUD else "local_process_tree",
                "api_cost_basis": op_row["api_cost_basis"], "api_cost_usd": expected_cost,
                "pricing_note_historical": raw.get("pricing_basis", raw.get("quota_basis", "not_applicable_local")),
                "installed_version": op_row["installed_version"], "pinned_version": op_row["pinned_version"],
                "model_versions": json.dumps(pair["adapter_version_snapshot"].get("model_versions", {}), ensure_ascii=False),
                "raw_mock": raw.get("mock"),
            })
            pair_dir = run_dir / "pairs" / tool / doc
            evaluated = read_json(pair_dir / "object_evaluation.json")
            matches = read_json(pair_dir / "matches.json")
            check(label + ":snapshot_objects", len(evaluated) == pair["object_count"] and all(close(o["object_score"], object_map[object_key(o)]["object_score"]) for o in evaluated))
            check(label + ":matched_counts", sum(m["matched"] for m in matches) == int(op_row["matched_objects"]) == pair["matched_count"]
                  and sum(not m["matched"] for m in matches) == int(op_row["missing_objects"]) == pair["missing_count"])
            cache = Path(pair["cache_dir"])
            raw_dir = Path(pair["paths"]["raw_dir"])
            check(
                label + ":cache_stages_present",
                raw_dir.is_dir()
                and (cache / "cache_manifest.json").is_file()
                and all(
                    Path(pair["paths"][key]).is_file()
                    for key in ("standardized", "normalized", "matches", "object_evaluation")
                ),
            )
            check(
                label + ":versioned_cache_provenance",
                pair["adapter_action"] != "standardize_only"
                or (cache.parent.name == "versions" and raw_dir == cache.parent.parent / "raw"),
            )
            normalized_path = Path(pair["paths"]["normalized"])
            norm = StandardizedDocument.model_validate_json(track(normalized_path).read_text(encoding="utf-8"))
            check(label + ":normalized_identity", norm.document_id == doc and len(norm.pages) == DOCS[doc])
            all_chem_count += sum(len(p.chemical_objects) for p in norm.pages)
            current_matches = matching.match_document([g for g in gt if g.document_id == doc], norm)
            check(label + ":reproduce_saved_matching", [m.model_dump(mode="json") for m in current_matches] == matches)
            match_counts[tool].update(m["match_method"] for m in matches if m["object_type"] == "text")
            for m in matches:
                if m["object_type"] in {"image", "diagram"}:
                    visual_contexts.append({"tool": tool, "document_id": doc, "object_id": m["object_id"], **m["context"]})
            if candidate["_dedup_fixed"] or not any(m["match_method"] == "text_multiblock_overlap" for m in matches):
                continue
            proposed_matches = {m.object_id: m for m in candidate["match_document"]([g for g in gt if g.document_id == doc], norm)}
            for original in matches:
                proposed = proposed_matches[original["object_id"]]
                if original["match_method"] != "text_multiblock_overlap":
                    check(label + ":unaffected_match:" + original["object_id"], original == proposed.model_dump(mode="json"))
                    continue
                ref = original["reference"]
                prior_score = float(object_map[tool, doc, original["object_id"]]["object_score"])
                recalculated = 100 * calculate_text_metrics(ref["text"], original["prediction"]["text"], reference_order=ref.get("reading_order"))["text_score"]
                new_score = 100 * calculate_text_metrics(ref["text"], proposed.prediction["text"], reference_order=ref.get("reading_order"))["text_score"]
                check(label + ":reproduce_fallback_text_score:" + original["object_id"], close(prior_score, recalculated))
                diagnostics.append({"tool": tool, "document_id": doc, "object_id": original["object_id"],
                    "baseline_blocks": original["context"]["source_block_count"], "proposed_blocks": proposed.context["source_block_count"],
                    "baseline_object_score": prior_score, "proposed_object_score_diagnostic_only": new_score,
                    "delta_diagnostic_only": new_score - prior_score})

    check("typed_chemistry_predictions", all_chem_count == 355)
    chemistry_rows = {row["tool"]: row for row in tool_rows}
    check(
        "chemistry_scores_and_split_reporting",
        all(
            sum(
                item["matched"] == "True"
                for item in object_rows
                if item["tool"] == tool and item["category"] == "chemistry"
            ) == 6
            and close(row["chemistry_score"], EXPECTED_CHEMISTRY[tool][0])
            and close(row["chemistry_detection_score"], 100.0)
            and close(row["chemistry_structured_extraction_score"], EXPECTED_CHEMISTRY[tool][1])
            for tool, row in chemistry_rows.items()
        ),
    )
    check("all_recorded_costs_zero", all(float(r["api_cost_usd"]) == 0 for r in pair_rows))
    check("cloud_runs_are_not_mocks", all(r["raw_mock"] is False for r in operational if r["kind"] == "cloud"))
    # Minimal reproduction: repeated words in separate boxes, plus a true duplicate.
    g = gt[0].model_copy(update={"page": 1, "bbox": BBox(x_min=0, y_min=0, x_max=1, y_max=1)})
    blocks = [TextBlock(element_id=str(i), page_number=1, raw_text="repeat", bbox=BBox(x_min=x, y_min=0.1, x_max=x+0.1, y_max=0.2)) for i, x in enumerate((0.1, 0.3, 0.1))]
    page = Page(page_number=1, width=100, height=100, text_blocks=blocks)
    candidates = matching.page_candidates(page, "text")
    old, _, _ = matching._aggregate_text_candidate(g, page, candidates, matching.MatchingConfig())
    new, _, _ = candidate["_aggregate_text_candidate"](g, page, candidates, matching.MatchingConfig())
    expected_old_count = 2 if candidate["_dedup_fixed"] else 1
    check("candidate_preserves_repetition_and_removes_true_duplicate", old.payload["source_block_count"] == expected_old_count and new.payload["source_block_count"] == 2)

    diagnostic_summary = []
    for tool in sorted({r["tool"] for r in diagnostics}):
        changes = {r["object_id"]: r for r in diagnostics if r["tool"] == tool}
        proposed_doc_scores = []
        for doc in DOCS:
            values = [changes[r["object_id"]]["proposed_object_score_diagnostic_only"] if r["object_id"] in changes else float(r["object_score"])
                      for r in object_rows if r["tool"] == tool and r["document_id"] == doc and r["category"] == "text"]
            proposed_doc_scores.append(mean(values))
        baseline = next(r for r in tool_rows if r["tool"] == tool)
        proposed_text = mean(proposed_doc_scores)
        diagnostic_summary.append({"tool": tool, "affected_objects": len(changes),
            "baseline_text_score": float(baseline["text_score"]), "proposed_text_score_diagnostic_only": proposed_text,
            "baseline_overall_score": float(baseline["overall_score"]),
            "proposed_overall_score_diagnostic_only": float(baseline["overall_score"]) + (proposed_text-float(baseline["text_score"]))/6})

    check("source_files_unchanged_during_audit", all(sha(ROOT / p) == h for p, h in HASHES.items()))
    write_csv("document_category_scores.csv", document_scores)
    write_csv("category_uncertainty.csv", uncertainty)
    write_csv("operational_provenance.csv", operational)
    write_csv("text_dedup_diagnostic.csv", diagnostics)
    write_csv("text_dedup_summary_diagnostic.csv", diagnostic_summary)
    write_csv("visual_detection_context.csv", visual_contexts)
    packages = {}
    for package in ("numpy", "pandas", "scipy", "matplotlib", "seaborn"):
        packages[package] = importlib.metadata.version(package) if importlib.util.find_spec(package) else None
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(), "experiment_id": EXPERIMENT,
        "structural_validation": "pass" if all(c["passed"] for c in CHECKS) else "fail",
        "prompt13_readiness": ("validation_failed" if not all(c["passed"] for c in CHECKS)
                               else "ready_with_documented_limitations" if candidate["_dedup_fixed"]
                               else "decision_required_text_matching_defect"),
        "checks_passed": sum(c["passed"] for c in CHECKS), "checks_failed": sum(not c["passed"] for c in CHECKS),
        "checks": CHECKS, "maximum_independent_category_score_difference": max_score_difference,
        "baseline_rows": {"tools": len(tool_rows), "pairs": len(pair_rows), "objects": len(object_rows)},
        "baseline_quality": quality, "gt_coverage": coverage_rows,
        "gt_text_objects_with_reading_order": sum(g.object_type == "text" and g.reference.get("reading_order") is not None for g in gt),
        "chemistry_predictions": all_chem_count, "text_match_methods": {k:dict(v) for k,v in match_counts.items()},
        "proposed_patch_applied": candidate["_dedup_fixed"], "diagnostic_score_changes": diagnostic_summary,
        "git_directory_present": (ROOT / ".git").exists(), "python_version": sys.version.split()[0], "analysis_packages": packages,
    }
    write_json("readiness_checks.json", summary)
    write_json("input_sha256.json", {"experiment_id": EXPERIMENT, "files": HASHES})
    print(json.dumps({k: summary[k] for k in ("structural_validation", "prompt13_readiness", "checks_passed", "checks_failed", "diagnostic_score_changes", "analysis_packages")}, ensure_ascii=True, indent=2))
    for c in CHECKS:
        if not c["passed"]:
            print("FAILED", c["check"], c["detail"])
    return 0 if all(c["passed"] for c in CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
