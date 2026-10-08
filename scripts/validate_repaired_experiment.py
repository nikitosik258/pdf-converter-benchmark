"""Offline control audit for a repaired, cached benchmark experiment.

The audit never calls an adapter acquisition method or a vendor API.  It reads
the completed experiment and its versioned downstream cache, independently
reproduces matching/evaluation/aggregation, verifies that the legacy cache is
byte-identical to a recorded source experiment, and writes review artifacts.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf_benchmark.benchmark.ground_truth import (  # noqa: E402
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)
from pdf_benchmark.benchmark.matching import match_document  # noqa: E402
from pdf_benchmark.evaluation.aggregation import aggregate_tool_scores  # noqa: E402
from pdf_benchmark.evaluation.framework import EvaluationFramework  # noqa: E402
from pdf_benchmark.evaluation.models import (  # noqa: E402
    EvaluationObjectInput,
    ObjectEvaluationResult,
)
from pdf_benchmark.evaluation.scoring import CATEGORY_ORDER, ScoringConfig  # noqa: E402
from pdf_benchmark.models import StandardizedDocument  # noqa: E402
from pdf_benchmark.normalization.latex import normalize_latex  # noqa: E402


TOOLS = (
    "pymupdf",
    "pdfplumber",
    "docling",
    "pdfminer",
    "mineru",
    "ocr_space",
    "nutrient",
    "mindee",
    "adobe_extract",
    "llamaparse",
)
CLOUD = {"ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse"}
DOCUMENT_PAGES = {"D01": 43, "D02": 7, "D03": 14, "D04": 12, "D05": 22}
TYPE_CATEGORY = {
    "text": "text",
    "table": "table",
    "math_formula": "math",
    "chemical_formula": "chemistry",
    "chemical_structure": "chemistry",
    "image": "image",
    "diagram": "diagram",
}
EXPECTED_CONTENT_COUNTS = {
    "pymupdf": {"text_blocks": 8056, "tables": 26, "formulas": 47, "chemical_objects": 31, "images": 630, "diagrams": 0},
    "pdfplumber": {"text_blocks": 32525, "tables": 25, "formulas": 101, "chemical_objects": 27, "images": 630, "diagrams": 0},
    "docling": {"text_blocks": 1095, "tables": 34, "formulas": 150, "chemical_objects": 30, "images": 6, "diagrams": 41},
    "pdfminer": {"text_blocks": 73001, "tables": 0, "formulas": 69, "chemical_objects": 31, "images": 54, "diagrams": 0},
    "mineru": {"text_blocks": 1135, "tables": 34, "formulas": 134, "chemical_objects": 29, "images": 32, "diagrams": 23},
    "ocr_space": {"text_blocks": 6110, "tables": 24, "formulas": 96, "chemical_objects": 66, "images": 0, "diagrams": 0},
    "nutrient": {"text_blocks": 1271, "tables": 34, "formulas": 140, "chemical_objects": 27, "images": 52, "diagrams": 0},
    "mindee": {"text_blocks": 35674, "tables": 0, "formulas": 111, "chemical_objects": 33, "images": 0, "diagrams": 0},
    "adobe_extract": {"text_blocks": 2172, "tables": 29, "formulas": 0, "chemical_objects": 36, "images": 487, "diagrams": 0},
    "llamaparse": {"text_blocks": 1218, "tables": 58, "formulas": 1251, "chemical_objects": 45, "images": 1, "diagrams": 0},
}

EXPECTED_MATH_SCORES = {
    "pymupdf": 21.957138474029,
    "pdfplumber": 19.851172824013,
    "docling": 36.277883881568,
    "pdfminer": 20.973583098842,
    "mineru": 48.022163052725,
    "ocr_space": 47.148120866985,
    "nutrient": 33.702367332585,
    "mindee": 14.385059620977,
    "adobe_extract": 0.0,
    "llamaparse": 49.333386215069,
}

EXPECTED_MATH_SCORE_CHANGES = {
    "pymupdf": 6,
    "pdfplumber": 6,
    "pdfminer": 6,
    "ocr_space": 6,
    "mindee": 6,
    "llamaparse": 1,
}

EXPECTED_CHEMISTRY_SCORES = {
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


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def close(left: Any, right: Any) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-8)


def object_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return row["tool"], row["document_id"], row["object_id"]


def normalized_asset_path(cache_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return cache_dir / Path(value.replace("\\", "/"))


def bbox_is_normalized(value: dict[str, Any] | None) -> bool:
    if value is None:
        return False
    return (
        0.0 <= float(value["x_min"]) <= float(value["x_max"]) <= 1.0
        and 0.0 <= float(value["y_min"]) <= float(value["y_max"]) <= 1.0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--source-experiment", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    for value in (args.experiment_id, args.source_experiment):
        if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in value):
            raise ValueError(f"Invalid experiment ID: {value!r}")

    benchmark_root = ROOT / "outputs" / "benchmark"
    experiment_dir = benchmark_root / "experiments" / args.experiment_id
    source_dir = benchmark_root / "experiments" / args.source_experiment
    output = (args.output or ROOT / "reports" / "benchmark_control" / args.experiment_id).resolve()
    output.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any = None) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    manifest = read_json(experiment_dir / "experiment_manifest.json")
    preflight = read_json(experiment_dir / "preflight_report.json")
    quality = read_json(experiment_dir / "results" / "data_quality_summary.json")
    tool_rows = read_csv(experiment_dir / "results" / "clean_results_dataset.csv")
    raw_rows = read_csv(experiment_dir / "results" / "raw_results_dataset.csv")
    pair_rows = read_csv(experiment_dir / "results" / "pair_operational_dataset.csv")
    object_rows = read_csv(experiment_dir / "results" / "object_results_dataset.csv")
    issues = read_csv(experiment_dir / "results" / "quality_issues.csv")

    check("experiment_status_clean", manifest.get("status") == quality.get("status") == "clean")
    check("preflight_passed", preflight.get("status") == "pass" and preflight.get("error_count", 0) == 0)
    check("row_counts_10_50_400", (len(tool_rows), len(pair_rows), len(object_rows)) == (10, 50, 400))
    check("active_roster", tuple(row["tool"] for row in tool_rows) == TOOLS and set(manifest.get("tool_runs", {})) == set(TOOLS))
    check("raw_equals_clean", raw_rows == tool_rows)
    check(
        "quality_summary_consistent",
        quality.get("raw_tool_rows") == quality.get("clean_tool_rows") == 10
        and quality.get("pair_rows") == 50
        and quality.get("object_rows") == 400
        and quality.get("error_count") == 0
        and quality.get("warning_count") == len(issues),
    )
    check(
        "warnings_are_retained_iqr_observations",
        len(issues) == int(quality.get("warning_count", -1))
        and all(row["severity"] == "warning" and row["code"] == "statistical_outlier_iqr" for row in issues),
        {"warning_count": len(issues)},
    )

    # Ground Truth is necessary for object-level quality scores.  Verify both
    # serialized forms and the immutable identity manifest independently.
    gt_json = read_json(ROOT / "ground_truth" / "objects.json")["objects"]
    gt_jsonl = read_jsonl(ROOT / "ground_truth" / "objects.jsonl")
    check("ground_truth_json_jsonl_equal", gt_json == gt_jsonl)
    gt = load_ground_truth(ROOT / "ground_truth")
    validate_ground_truth_manifest(gt, load_object_manifest(ROOT / "benchmark" / "object_manifest.json"))
    check("ground_truth_manifest_valid", len(gt) == 40 and len({item.object_id for item in gt}) == 40)
    gt_map = {item.object_id: item for item in gt}
    target_gt = {item.object_id: item for item in gt}
    check(
        "targeted_gt_repairs_present",
        all(target_gt[key].reference["latex"].startswith("{}_{t}") for key in ("MATH_002", "MATH_003", "MATH_004"))
        and target_gt["MATH_005"].bbox.model_dump(mode="json") == {"x_min": 0.310924, "y_min": 0.611639, "x_max": 0.815126, "y_max": 0.659145}
        and "n_{21}" in target_gt["MATH_006"].reference["latex"]
        and target_gt["CHEM_001"].bbox.model_dump(mode="json") == {"x_min": 0.292984, "y_min": 0.146948, "x_max": 0.386878, "y_max": 0.165088}
        and target_gt["CHEM_002"].bbox.model_dump(mode="json") == {"x_min": 0.29518, "y_min": 0.163128, "x_max": 0.49268, "y_max": 0.181268},
    )
    for relative, expected in preflight.get("ground_truth_hashes", {}).items():
        current = ROOT / relative
        check(f"preflight_gt_hash:{relative}", current.is_file() and sha256(current) == expected)
    for document in preflight.get("pdfs", []):
        current = ROOT / "documents" / document["filename"]
        check(
            f"preflight_pdf_hash:{document['document_id']}",
            current.is_file() and current.stat().st_size == document["size_bytes"] and sha256(current) == document["sha256"],
        )

    pair_map = {(row["tool"], row["document_id"]): row for row in pair_rows}
    object_map = {object_key(row): row for row in object_rows}
    expected_pairs = {(tool, document) for tool in TOOLS for document in DOCUMENT_PAGES}
    expected_objects = {(tool, item.document_id, item.object_id) for tool in TOOLS for item in gt}
    check("complete_unique_pair_grid", len(pair_map) == 50 and set(pair_map) == expected_pairs)
    check("complete_unique_object_grid", len(object_map) == 400 and set(object_map) == expected_objects)
    check(
        "object_identity_ranges_and_missing_zero",
        all(
            row["document_id"] == gt_map[row["object_id"]].document_id
            and int(row["page"]) == gt_map[row["object_id"]].page
            and row["object_type"] == gt_map[row["object_id"]].object_type
            and row["category"] == TYPE_CATEGORY[row["object_type"]]
            and math.isfinite(float(row["object_score"]))
            and 0.0 <= float(row["object_score"]) <= 100.0
            and row["matched"] in {"True", "False"}
            and (row["matched"] == "True" or close(row["object_score"], 0.0))
            for row in object_rows
        ),
    )

    scoring_config = ScoringConfig.from_yaml(ROOT / "config" / "metrics.yaml")
    evaluator = EvaluationFramework(scoring_config)
    coverage: collections.Counter[tuple[str, str]] = collections.Counter(
        (item.document_id, TYPE_CATEGORY[item.object_type]) for item in gt
    )
    content_counts: dict[str, collections.Counter[str]] = {tool: collections.Counter() for tool in TOOLS}
    all_formula_payloads: list[str] = []
    enriched_formula_provenance: list[dict[str, Any]] = []
    visual_contexts: list[dict[str, Any]] = []
    operational_rows: list[dict[str, Any]] = []
    pair_provenance_rows: list[dict[str, Any]] = []
    documents_by_tool: dict[str, list[tuple[dict[str, Any], Path]]] = collections.defaultdict(list)

    for tool in TOOLS:
        run_id = manifest["tool_runs"][tool]
        run_dir = benchmark_root / "runs" / run_id
        run_manifest = read_json(run_dir / "run_manifest.json")
        report = read_json(run_dir / "metrics" / "evaluation_report.json")
        integrity = read_json(experiment_dir / "integrity" / f"{tool}.json")
        saved_pairs = read_jsonl(run_dir / "pair_results.jsonl")
        check(f"{tool}:run_and_integrity_clean", run_manifest.get("status") == "success" and integrity.get("status") == "clean")
        check(f"{tool}:five_saved_pairs", len(saved_pairs) == 5)

        reproduced_objects: list[ObjectEvaluationResult] = []
        for pair in saved_pairs:
            document = pair["document_id"]
            label = f"{tool}/{document}"
            operational = pair_map[tool, document]
            pair_dir = run_dir / "pairs" / tool / document
            cache_dir = Path(pair["cache_dir"]).resolve()
            expected_cache_root = (benchmark_root / "cache" / tool / document / "versions").resolve()
            raw_dir = Path(pair["paths"]["raw_dir"]).resolve()
            expected_raw_dir = (benchmark_root / "cache" / tool / document / "raw").resolve()
            cache_manifest = read_json(cache_dir / "cache_manifest.json")

            check(
                f"{label}:cached_without_acquisition",
                pair.get("status") == operational["status"] == "success"
                and operational["adapter_action"] == pair.get("adapter_action")
                and pair.get("adapter_action") in {"reuse_standardized", "standardize_only"},
            )
            check(
                f"{label}:versioned_downstream_cache",
                cache_dir.parent == expected_cache_root and raw_dir == expected_raw_dir,
                {"cache_dir": str(cache_dir), "raw_dir": str(raw_dir)},
            )
            check(
                f"{label}:stage_fingerprints_match_manifest",
                pair.get("stage_fingerprints") == cache_manifest.get("stage_fingerprints"),
            )
            check(
                f"{label}:all_stage_files_exist",
                all(Path(pair["paths"][key]).is_file() for key in ("standardized", "normalized", "matches", "object_evaluation"))
                and (expected_raw_dir.parent / "raw_result_snapshot.json").is_file(),
            )
            if tool in CLOUD:
                check(
                    f"{label}:cloud_api_not_recalled",
                    pair.get("adapter_action") == "reuse_standardized"
                    or "cloud_api_not_recalled" in pair.get("resume_notes", []),
                )

            normalized_path = Path(pair["paths"]["normalized"])
            normalized_raw = read_json(normalized_path)
            normalized = StandardizedDocument.model_validate(normalized_raw)
            documents_by_tool[tool].append((normalized_raw, normalized_path.parent))
            check(f"{label}:normalized_identity", normalized.document_id == document and len(normalized.pages) == DOCUMENT_PAGES[document])

            saved_matches = read_json(pair_dir / "matches.json")
            saved_evaluations = read_json(pair_dir / "object_evaluation.json")
            current_matches = match_document([item for item in gt if item.document_id == document], normalized)
            current_match_payloads = [item.model_dump(mode="json") for item in current_matches]
            check(f"{label}:matching_reproduced", current_match_payloads == saved_matches)

            current_evaluations = []
            for match in current_matches:
                request = EvaluationObjectInput(
                    tool=tool,
                    document_id=document,
                    page=match.page,
                    object_id=match.object_id,
                    object_type=match.object_type,
                    reference=match.reference,
                    prediction=match.prediction,
                    context=match.context,
                )
                current_evaluations.append(evaluator.evaluate_object(request))
            current_evaluation_payloads = [item.model_dump(mode="json") for item in current_evaluations]
            check(f"{label}:object_evaluation_reproduced", current_evaluation_payloads == saved_evaluations)
            check(
                f"{label}:csv_object_scores_reproduced",
                all(
                    object_key(row) in object_map
                    and close(row["object_score"], object_map[object_key(row)]["object_score"])
                    and str(row["matched"]) == object_map[object_key(row)]["matched"]
                    for row in saved_evaluations
                ),
            )
            check(
                f"{label}:match_counts_consistent",
                sum(item.matched for item in current_matches) == pair["matched_count"] == int(operational["matched_objects"])
                and sum(not item.matched for item in current_matches) == pair["missing_count"] == int(operational["missing_objects"]),
            )
            reproduced_objects.extend(current_evaluations)

            for page in normalized_raw["pages"]:
                for field in content_counts[tool] or EXPECTED_CONTENT_COUNTS[tool]:
                    content_counts[tool][field] += len(page.get(field, []))
                for formula in page.get("formulas", []):
                    if str(formula.get("element_id", "")).startswith("shared_math_"):
                        enriched_formula_provenance.append(formula.get("provenance", {}))
                    for key in ("latex", "normalized_latex", "raw_text"):
                        value = formula.get(key)
                        if isinstance(value, str) and value:
                            all_formula_payloads.append(value)
            for match in saved_matches:
                if match["object_type"] in {"image", "diagram"}:
                    visual_contexts.append({"tool": tool, "document_id": document, "object_id": match["object_id"], **match["context"]})

            raw_metadata = pair.get("adapter_raw_metadata", {})
            usage = pair.get("adapter_resource_usage", {})
            timing_field = next(
                (key for key in ("processing_seconds", "latency_seconds") if isinstance(raw_metadata.get(key), (int, float)) and raw_metadata[key] > 0),
                None,
            ) if tool in CLOUD else None
            expected_elapsed = raw_metadata[timing_field] if timing_field else usage["wall_time_seconds"]
            process_gpu = usage.get("gpu_peak_process_mb") or 0.0
            torch_active = (usage.get("torch_peak_allocated_mb") or 0.0) > 0 or (usage.get("torch_peak_reserved_mb") or 0.0) > 0
            expected_vram = process_gpu if process_gpu > 0 else (usage.get("gpu_peak_device_used_mb") or 0.0) if torch_active else 0.0
            expected_ram = usage.get("peak_process_tree_rss_mb", usage.get("peak_rss_mb"))
            cost_field = "actual_cost_usd" if raw_metadata.get("actual_cost_usd") is not None else "estimated_cost_usd"
            expected_cost = raw_metadata.get(cost_field, 0.0) if tool in CLOUD else 0.0
            check(
                f"{label}:operational_provenance",
                close(operational["processing_seconds"], expected_elapsed)
                and close(operational["peak_ram_mb"], expected_ram)
                and close(operational["peak_vram_mb"], expected_vram)
                and close(operational["api_cost_usd"], expected_cost),
            )
            operational_rows.append(
                {
                    "tool": tool,
                    "document_id": document,
                    "processing_basis": f"raw.{timing_field}" if timing_field else "adapter_resource_usage.wall_time_seconds",
                    "ram_basis": "cloud_client" if tool in CLOUD else "local_process_tree",
                    "vram_basis": "process_gpu" if process_gpu > 0 else "device_used_with_torch_activity" if expected_vram else "no_attributable_gpu",
                    "cost_basis": operational["api_cost_basis"],
                    "historical_adapter_seconds": expected_elapsed,
                    "current_standardization_seconds": pair.get("standardization_resource_usage_current_invocation", {}).get("wall_time_seconds"),
                    "api_cost_usd": expected_cost,
                }
            )
            pair_provenance_rows.append(
                {
                    "tool": tool,
                    "document_id": document,
                    "adapter_action": pair["adapter_action"],
                    "cloud_api_not_recalled": (
                        tool not in CLOUD
                        or pair.get("adapter_action") == "reuse_standardized"
                        or "cloud_api_not_recalled" in pair.get("resume_notes", [])
                    ),
                    "raw_cache_dir": str(raw_dir),
                    "versioned_cache_dir": str(cache_dir),
                    **{f"fingerprint_{key}": value for key, value in pair["stage_fingerprints"].items()},
                }
            )

        aggregate = aggregate_tool_scores(reproduced_objects, config=scoring_config)
        stored_aggregate = {
            row["score_name"]: row
            for row in report["aggregate_scores"]
            if row["scope"] in {"category", "tool"}
        }
        check(
            f"{tool}:aggregate_scores_and_ci_reproduced",
            all(
                record.score_name in stored_aggregate
                and close(record.score, stored_aggregate[record.score_name]["score"])
                and (
                    record.ci95_low is None
                    or (
                        close(record.ci95_low, stored_aggregate[record.score_name]["ci95_low"])
                        and close(record.ci95_high, stored_aggregate[record.score_name]["ci95_high"])
                    )
                )
                for record in aggregate
            ),
        )
        tool_row = next(row for row in tool_rows if row["tool"] == tool)
        check(
            f"{tool}:reported_tool_scores_reproduced",
            all(
                close(tool_row[f"{category}_score"], stored_aggregate[f"{category}_score"]["score"])
                for category in CATEGORY_ORDER
            )
            and close(tool_row["overall_score"], stored_aggregate["overall_quality_score"]["score"])
            and close(
                tool_row["chemistry_detection_score"],
                stored_aggregate["chemistry_detection_score"]["score"],
            )
            and (
                (
                    not tool_row["chemistry_structured_extraction_score"]
                    and "chemistry_structured_extraction_score" not in stored_aggregate
                )
                or close(
                    tool_row["chemistry_structured_extraction_score"],
                    stored_aggregate["chemistry_structured_extraction_score"]["score"],
                )
            ),
        )

    check(
        "all_pairs_reused_cached_stages",
        all(row["adapter_action"] in {"reuse_standardized", "standardize_only"} for row in pair_provenance_rows),
    )
    check("zero_adapter_acquisitions", sum(row["adapter_action"] == "run_adapter" for row in pair_provenance_rows) == 0)
    check("zero_cloud_api_calls", all(row["cloud_api_not_recalled"] for row in pair_provenance_rows if row["tool"] in CLOUD))
    check("content_counts_match_repaired_outputs", {tool: dict(content_counts[tool]) for tool in TOOLS} == EXPECTED_CONTENT_COUNTS)
    check("latex_normalization_idempotent", all(normalize_latex(normalize_latex(value)) == normalize_latex(value) for value in all_formula_payloads), {"payloads": len(all_formula_payloads)})
    check(
        "shared_math_predictions_are_gt_independent",
        bool(enriched_formula_provenance)
        and all(
            item.get("gt_independent") is True
            and item.get("source")
            in {
                "explicit_delimited_latex_v1",
                "numbered_delimited_display_group_v1",
                "numbered_equation_layout_v1",
            }
            for item in enriched_formula_provenance
        ),
        {"predictions": len(enriched_formula_provenance)},
    )
    check(
        "typed_chemistry_predictions_use_shared_explicit_evidence",
        sum(content_counts[tool]["chemical_objects"] for tool in TOOLS) == 355
        and all(content_counts[tool]["chemical_objects"] > 0 for tool in TOOLS),
    )
    chemistry_rows = {row["tool"]: row for row in tool_rows}
    check(
        "chemistry_scores_and_split_reporting",
        all(
            close(chemistry_rows[tool]["chemistry_score"], EXPECTED_CHEMISTRY_SCORES[tool][0])
            and close(chemistry_rows[tool]["chemistry_detection_score"], 100.0)
            and close(
                chemistry_rows[tool]["chemistry_structured_extraction_score"],
                EXPECTED_CHEMISTRY_SCORES[tool][1],
            )
            for tool in TOOLS
        ),
    )
    check(
        "math_scores_match_gt_independent_repair",
        all(close(chemistry_rows[tool]["math_score"], EXPECTED_MATH_SCORES[tool]) for tool in TOOLS),
    )
    check("visual_context_count", len(visual_contexts) == 120)
    check(
        "visual_sampled_gt_recall_policy",
        all(
            row.get("detection_policy") == "sampled_gt_recall_v1"
            and "detection_fp" not in row
            and set(row) >= {"detection_tp", "detection_fn", "candidate_count", "gt_count", "unmatched_candidate_count"}
            for row in visual_contexts
        ),
    )

    # Adapter-specific evidence for the repaired representations.
    def objects(tool: str, field: str) -> list[tuple[dict[str, Any], Path]]:
        return [(item, base) for document, base in documents_by_tool[tool] for page in document["pages"] for item in page.get(field, [])]

    mineru_tables = objects("mineru", "tables")
    mineru_visuals = objects("mineru", "images") + objects("mineru", "diagrams")
    adobe_tables = objects("adobe_extract", "tables")
    adobe_images = objects("adobe_extract", "images")
    docling_visuals = objects("docling", "images") + objects("docling", "diagrams")
    docling_diagrams = objects("docling", "diagrams")
    mindee_text = objects("mindee", "text_blocks")
    pdfminer_text = objects("pdfminer", "text_blocks")
    pymupdf_text = objects("pymupdf", "text_blocks")
    nutrient_objects = sum((objects("nutrient", field) for field in ("text_blocks", "tables", "formulas", "images")), [])
    check("repair_mineru_string_tables", len(mineru_tables) == 34 and all(item.get("cells") for item, _ in mineru_tables))
    check(
        "repair_mineru_visual_assets",
        len(mineru_visuals) == 55 and all((path := normalized_asset_path(base, item.get("asset_path"))) is not None and path.is_file() for item, base in mineru_visuals),
    )
    check("repair_adobe_indexed_tables", len(adobe_tables) == 29 and all(item.get("cells") for item, _ in adobe_tables))
    check(
        "repair_adobe_visual_assets",
        sum(bool(item.get("asset_path")) for item, _ in adobe_images) == 232
        and all((path := normalized_asset_path(base, item.get("asset_path"))) is None or path.is_file() for item, base in adobe_images),
    )
    check(
        "repair_docling_picture_classification_and_assets",
        len(docling_visuals) == 47 and all((path := normalized_asset_path(base, item.get("asset_path"))) is not None and path.is_file() for item, base in docling_visuals),
    )
    check("repair_docling_picture_child_text", sum(bool(item.get("text_elements")) for item, _ in docling_diagrams) == 35)
    check(
        "repair_mindee_word_level_text",
        len(mindee_text) == 35674
        and all(item.get("provenance", {}).get("source") == "Mindee OCR pages.words" and "word_index" in item.get("provenance", {}) for item, _ in mindee_text),
    )
    check(
        "repair_pdfminer_text_hierarchy",
        len(pdfminer_text) == 73001
        and all(item.get("provenance", {}).get("text_selection") == "verified_text_hierarchy_v1" for item, _ in pdfminer_text),
    )
    check(
        "repair_pymupdf_native_lines",
        len(pymupdf_text) == 8056
        and all(item.get("provenance", {}).get("text_assembly") == "native_span_concatenation_v1" and "pymupdf_line_index" in item.get("provenance", {}) for item, _ in pymupdf_text),
    )
    check(
        "repair_nutrient_coordinate_canvas",
        len(nutrient_objects) == 1497 and all(bbox_is_normalized(item.get("bbox")) for item, _ in nutrient_objects),
    )

    # Prove that the shared legacy cache used as input was not modified.  New
    # downstream files live only below versions/ and are intentionally absent
    # from the historical source hash list.
    provenance_dir = source_dir
    seen_provenance: set[str] = set()
    source_hashes_path = provenance_dir / "source_input_hashes.json"
    while not source_hashes_path.is_file():
        provenance_id = provenance_dir.name
        if provenance_id in seen_provenance:
            raise ValueError("Cycle in legacy-cache provenance chain")
        seen_provenance.add(provenance_id)
        source_control = read_json(provenance_dir / "control_prompt12_summary.json")
        provenance_source_id = source_control.get("source_experiment_id")
        if not provenance_source_id:
            raise ValueError("Source experiment has no legacy-cache provenance chain")
        provenance_dir = benchmark_root / "experiments" / provenance_source_id
        source_hashes_path = provenance_dir / "source_input_hashes.json"
    source_hashes = read_json(source_hashes_path)
    legacy_hashes = {
        relative: expected
        for relative, expected in source_hashes.items()
        if relative.replace("\\", "/").startswith("outputs/benchmark/cache/")
        and "/versions/" not in relative.replace("\\", "/")
    }
    missing_legacy: list[str] = []
    changed_legacy: list[str] = []
    for relative, expected in legacy_hashes.items():
        path = ROOT / relative
        if not path.is_file():
            missing_legacy.append(relative)
        elif sha256(path) != expected:
            changed_legacy.append(relative)
    check(
        "legacy_cache_byte_identical",
        len(legacy_hashes) == 2284 and not missing_legacy and not changed_legacy,
        {"files_checked": len(legacy_hashes), "missing": len(missing_legacy), "changed": len(changed_legacy)},
    )

    old_tool_rows = {row["tool"]: row for row in read_csv(source_dir / "results" / "clean_results_dataset.csv")}
    score_change_rows: list[dict[str, Any]] = []
    for row in tool_rows:
        old = old_tool_rows[row["tool"]]
        result: dict[str, Any] = {"tool": row["tool"]}
        for score in (*CATEGORY_ORDER, "overall"):
            field = f"{score}_score"
            result[f"old_{field}"] = float(old[field])
            result[f"new_{field}"] = float(row[field])
            result[f"delta_{field}"] = float(row[field]) - float(old[field])
        score_change_rows.append(result)

    old_object_rows = {object_key(row): row for row in read_csv(source_dir / "results" / "object_results_dataset.csv")}
    object_change_rows: list[dict[str, Any]] = []
    for row in object_rows:
        old = old_object_rows[object_key(row)]
        delta = float(row["object_score"]) - float(old["object_score"])
        if abs(delta) <= 1e-9:
            continue
        object_change_rows.append(
            {
                "tool": row["tool"],
                "document_id": row["document_id"],
                "object_id": row["object_id"],
                "category": row["category"],
                "old_matched": old["matched"],
                "new_matched": row["matched"],
                "old_score": float(old["object_score"]),
                "new_score": float(row["object_score"]),
                "delta": delta,
            }
        )
    check(
        "score_changes_retained_without_selection",
        len(object_change_rows) == sum(EXPECTED_MATH_SCORE_CHANGES.values())
        and sum(row["delta"] > 0 for row in object_change_rows) == sum(EXPECTED_MATH_SCORE_CHANGES.values())
        and sum(row["delta"] < 0 for row in object_change_rows) == 0
        and all(row["category"] == "math" for row in object_change_rows)
        and collections.Counter(row["tool"] for row in object_change_rows) == EXPECTED_MATH_SCORE_CHANGES,
    )

    write_csv(output / "pair_cache_provenance.csv", pair_provenance_rows)
    write_csv(output / "operational_provenance.csv", operational_rows)
    write_csv(output / "tool_score_changes.csv", score_change_rows)
    write_csv(output / "object_score_changes.csv", object_change_rows)
    write_csv(output / "warnings_retained.csv", issues)
    write_csv(output / "visual_detection_context.csv", visual_contexts)
    write_json(
        output / "legacy_cache_preservation.json",
        {
            "source_experiment": args.source_experiment,
            "files_checked": len(legacy_hashes),
            "missing": missing_legacy,
            "changed": changed_legacy,
        },
    )
    write_json(output / "content_counts.json", {tool: dict(content_counts[tool]) for tool in TOOLS})

    passed = all(item["passed"] for item in checks)
    summary = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": args.experiment_id,
        "source_experiment_id": args.source_experiment,
        "status": "pass" if passed else "fail",
        "offline_cached_recomputation": {
            "pairs": len(pair_provenance_rows),
            "standardize_only": sum(row["adapter_action"] == "standardize_only" for row in pair_provenance_rows),
            "reuse_standardized": sum(row["adapter_action"] == "reuse_standardized" for row in pair_provenance_rows),
            "adapter_acquisitions": 0 if all(row["adapter_action"] != "run_adapter" for row in pair_provenance_rows) else None,
            "cloud_api_calls": 0 if all(row["cloud_api_not_recalled"] for row in pair_provenance_rows if row["tool"] in CLOUD) else None,
            "legacy_cache_files_verified_unchanged": len(legacy_hashes),
        },
        "dataset": {
            "tools": len(tool_rows),
            "pairs": len(pair_rows),
            "objects": len(object_rows),
            "errors": quality["error_count"],
            "warnings": quality["warning_count"],
        },
        "score_changes": {
            "changed_object_rows": len(object_change_rows),
            "improved": sum(row["delta"] > 0 for row in object_change_rows),
            "worsened": sum(row["delta"] < 0 for row in object_change_rows),
            "unchanged": len(object_rows) - len(object_change_rows),
        },
        "formula_payloads_checked_for_idempotence": len(all_formula_payloads),
        "chemistry_predictions": sum(content_counts[tool]["chemical_objects"] for tool in TOOLS),
        "checks_passed": sum(item["passed"] for item in checks),
        "checks_failed": sum(not item["passed"] for item in checks),
        "checks": checks,
    }
    write_json(output / "summary.json", summary)
    print(json.dumps({key: summary[key] for key in ("status", "experiment_id", "offline_cached_recomputation", "dataset", "score_changes", "checks_passed", "checks_failed")}, ensure_ascii=False, indent=2))
    for item in checks:
        if not item["passed"]:
            print("FAILED", item["check"], item["detail"])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
