from __future__ import annotations

import argparse
import copy
import csv
import json
from pathlib import Path

from pdf_benchmark.benchmark.cache import PairCache, cache_compatibility, sha256_file, sha256_json
from pdf_benchmark.benchmark.config import BenchmarkConfig, resolve_document_path
from pdf_benchmark.benchmark.runner import BenchmarkRunner, _tool_config
from pdf_benchmark.benchmark.registry import TOOL_SPECS
from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.evaluation.models import EvaluationObjectInput
from pdf_benchmark.models import StandardizedDocument
from pdf_benchmark.normalization.pipeline import normalize_document
from pdf_benchmark.benchmark.matching import match_document


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _metric(result, name: str) -> float | None:
    return next(
        (record.raw_value for record in result.metrics if record.metric_name == name),
        None,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    config = BenchmarkConfig.from_yaml(project_root / "config" / "benchmark.yaml")
    runner = BenchmarkRunner(project_root=project_root, config=config)
    args.output.mkdir(parents=True, exist_ok=True)

    baseline_path = (
        project_root
        / "outputs/benchmark/experiments/20261001T200058Z_s42_textdedup/results"
        / "object_results_dataset.csv"
    )
    with baseline_path.open(encoding="utf-8-sig", newline="") as handle:
        baseline = {
            (row["tool"], row["object_id"]): float(row["object_score"])
            for row in csv.DictReader(handle)
            if row["category"] == "math"
        }

    framework = EvaluationFramework(runner.scoring_config)
    math_rows: list[dict] = []
    for tool in config.tools:
        for document_id in config.document_map():
            gt = runner._gt_for_document(document_id)
            if not any(item.object_type == "math_formula" for item in gt):
                continue
            standardized_path = (
                project_root
                / config.output_root
                / "cache"
                / tool
                / document_id
                / "standardized.json"
            )
            standardized = StandardizedDocument.model_validate_json(
                standardized_path.read_text(encoding="utf-8")
            )
            normalized = normalize_document(standardized, runner.normalization_config)
            matches = match_document(gt, normalized, config=runner.matching_config)
            for match in matches:
                if match.object_type != "math_formula":
                    continue
                fixed_request = EvaluationObjectInput(
                    tool=tool,
                    document_id=document_id,
                    page=match.page,
                    object_id=match.object_id,
                    object_type=match.object_type,
                    reference=match.reference,
                    prediction=match.prediction,
                    context=match.context,
                )
                fixed = framework.evaluate_object(fixed_request)

                old_prediction = copy.deepcopy(match.prediction)
                if old_prediction is not None and old_prediction.get("normalized_latex") is not None:
                    old_prediction["latex"] = old_prediction["normalized_latex"]
                old_bug = framework.evaluate_object(
                    fixed_request.model_copy(update={"prediction": old_prediction})
                )
                prediction = match.prediction or {}
                math_rows.append(
                    {
                        "tool": tool,
                        "document_id": document_id,
                        "object_id": match.object_id,
                        "matched": match.matched,
                        "prediction_latex_raw": prediction.get("latex"),
                        "prediction_latex_normalized": prediction.get("normalized_latex"),
                        "fixed_raw_exact_match": _metric(fixed, "raw_exact_match"),
                        "fixed_normalized_exact_match": _metric(
                            fixed, "normalized_exact_match"
                        ),
                        "old_bug_score": old_bug.object_score,
                        "fixed_score": fixed.object_score,
                        "fixed_minus_old_bug": fixed.object_score - old_bug.object_score,
                        "historical_baseline_score": baseline.get((tool, match.object_id)),
                    }
                )

    cache_rows: list[dict] = []
    for tool in config.tools:
        spec = TOOL_SPECS[tool]
        for document_id, document_config in config.document_map().items():
            pdf_path = resolve_document_path(project_root, document_config)
            tool_config, _ = _tool_config(project_root, tool)
            source_sha256 = sha256_file(pdf_path)
            config_sha256 = sha256_json(tool_config)
            fingerprints = runner._stage_fingerprints(
                tool=tool,
                source_sha256=source_sha256,
                tool_config_sha256=config_sha256,
            )
            legacy = PairCache(
                project_root / config.output_root / "cache" / tool / document_id
            )
            compatibility = cache_compatibility(
                legacy,
                source_sha256=source_sha256,
                tool_config_sha256=config_sha256,
                stage_fingerprints=fingerprints,
            )
            action, notes = runner._plan_adapter_action(
                spec=spec,
                raw_exists=legacy.raw_snapshot.exists(),
                standardized_exists=legacy.standardized.exists(),
                raw_compatible=compatibility.raw_compatible,
                standardized_compatible=compatibility.standardized_compatible,
                incompatibility_reasons=compatibility.reasons,
            )
            cache_rows.append(
                {
                    "tool": tool,
                    "document_id": document_id,
                    "raw_compatible": compatibility.raw_compatible,
                    "standardized_compatible": compatibility.standardized_compatible,
                    "planned_action": action,
                    "would_call_adapter_acquisition": action == "run_adapter",
                    "would_call_cloud_api": spec.kind == "cloud" and action == "run_adapter",
                    "versioned_cache_id": sha256_json(fingerprints)[:24],
                    "reasons": "|".join(compatibility.reasons),
                    "notes": "|".join(notes),
                }
            )

    _write_csv(args.output / "math_payload_scores.csv", math_rows)
    _write_csv(args.output / "cache_plan.csv", cache_rows)
    summary = {
        "math": {
            "object_rows": len(math_rows),
            "matched_rows": sum(bool(row["matched"]) for row in math_rows),
            "rows_changed_by_raw_payload_fix": sum(
                abs(float(row["fixed_minus_old_bug"])) > 1e-12 for row in math_rows
            ),
            "raw_exact_matches_removed": sum(
                row["fixed_raw_exact_match"] == 0
                and row["fixed_normalized_exact_match"] == 1
                for row in math_rows
            ),
        },
        "cache": {
            "pair_rows": len(cache_rows),
            "raw_compatible": sum(bool(row["raw_compatible"]) for row in cache_rows),
            "standardized_invalidated": sum(
                not bool(row["standardized_compatible"]) for row in cache_rows
            ),
            "standardize_only": sum(
                row["planned_action"] == "standardize_only" for row in cache_rows
            ),
            "adapter_acquisitions": sum(
                bool(row["would_call_adapter_acquisition"]) for row in cache_rows
            ),
            "cloud_api_calls": sum(bool(row["would_call_cloud_api"]) for row in cache_rows),
        },
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
