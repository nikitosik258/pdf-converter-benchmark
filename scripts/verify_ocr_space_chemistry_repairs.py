"""Verify OCR.Space chemistry extraction from cached raw responses only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from pdf_benchmark.adapters.cloud import OCRSpaceAdapter
from pdf_benchmark.benchmark.cache import PairCache, cache_compatibility, sha256_file, sha256_json
from pdf_benchmark.benchmark.config import BenchmarkConfig, resolve_document_path
from pdf_benchmark.benchmark.matching import match_document
from pdf_benchmark.benchmark.registry import TOOL_SPECS
from pdf_benchmark.benchmark.runner import BenchmarkRunner, _pair_object_results, _tool_config
from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.evaluation.aggregation import aggregate_benchmark
from pdf_benchmark.evaluation.models import ObjectEvaluationResult
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.normalization.pipeline import normalize_document
from pdf_benchmark.utils.io import write_json


EXPERIMENT_ID = "20261002T222803Z_s42_14c5cd35"
DOCUMENTS = ("D01", "D02", "D03", "D04", "D05")
BEFORE_REPAIR = Path("outputs/repairs/ocr_space_text_blocks_v1")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _hash_tree(paths: list[Path], *, root: Path) -> dict[str, str]:
    files: set[Path] = set()
    for path in paths:
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(item for item in path.rglob("*") if item.is_file())
    return {
        path.resolve().relative_to(root.resolve()).as_posix(): _sha256(path)
        for path in sorted(files)
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _aggregate_value(results: list[ObjectEvaluationResult], score_name: str, scoring_config) -> float:
    report = aggregate_benchmark(results, config=scoring_config)
    return next(record.score for record in report.aggregate_scores if record.score_name == score_name)


def _aggregate_optional(
    results: list[ObjectEvaluationResult],
    score_name: str,
    scoring_config,
) -> float | None:
    report = aggregate_benchmark(results, config=scoring_config)
    return next(
        (record.score for record in report.aggregate_scores if record.score_name == score_name),
        None,
    )


def _object_metric(result: ObjectEvaluationResult, metric_name: str) -> float | None:
    return next(
        (metric.metric_value for metric in result.metrics if metric.metric_name == metric_name),
        None,
    )


def _without_order_index(payload: dict) -> dict:
    result = dict(payload)
    result.pop("order_index", None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/repairs/ocr_space_chemistry_objects_v1"),
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty repair directory: {output}")
    output.mkdir(parents=True, exist_ok=True)

    before_root = root / BEFORE_REPAIR
    if not before_root.exists():
        raise FileNotFoundError(f"Missing prior isolated text repair: {before_root}")

    config = BenchmarkConfig.from_yaml(root / "config/benchmark.yaml")
    runner = BenchmarkRunner(project_root=root, config=config)
    framework = EvaluationFramework(runner.scoring_config)
    tool_config, _ = _tool_config(root, "ocr_space")
    adapter = OCRSpaceAdapter(tool_config)

    experiment_dir = root / config.output_root / "experiments" / EXPERIMENT_ID
    experiment_manifest = json.loads(
        (experiment_dir / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    run_id = experiment_manifest["tool_runs"]["ocr_space"]

    protected_paths = [
        experiment_dir,
        root / config.output_root / "runs" / run_id,
        root / config.output_root / "latest_experiment.txt",
        before_root,
        root / "documents",
        root / "ground_truth",
        root / "benchmark" / "object_manifest.json",
    ]
    for document_id in DOCUMENTS:
        cache_root = root / config.output_root / "cache" / "ocr_space" / document_id
        protected_paths.extend([cache_root / "raw", cache_root / "raw_result_snapshot.json"])
        pair = json.loads(
            (
                root / config.output_root / "runs" / run_id / "pairs" / "ocr_space"
                / document_id / "pair_result.json"
            ).read_text(encoding="utf-8")
        )
        protected_paths.append(Path(pair["cache_dir"]))
    before_hashes = _hash_tree(protected_paths, root=root)

    cache_rows: list[dict] = []
    for document_config in config.documents:
        document_id = document_config.document_id
        pdf_path = resolve_document_path(root, document_config)
        cache = PairCache(root / config.output_root / "cache" / "ocr_space" / document_id)
        fingerprints = runner._stage_fingerprints(
            tool="ocr_space",
            source_sha256=sha256_file(pdf_path),
            tool_config_sha256=sha256_json(tool_config),
        )
        compatibility = cache_compatibility(
            cache,
            source_sha256=sha256_file(pdf_path),
            tool_config_sha256=sha256_json(tool_config),
            stage_fingerprints=fingerprints,
        )
        action, notes = runner._plan_adapter_action(
            spec=TOOL_SPECS["ocr_space"],
            raw_exists=cache.raw_snapshot.exists(),
            standardized_exists=cache.standardized.exists(),
            raw_compatible=compatibility.raw_compatible,
            standardized_compatible=compatibility.standardized_compatible,
            incompatibility_reasons=compatibility.reasons,
        )
        if not compatibility.raw_compatible or action != "standardize_only":
            raise AssertionError(
                f"{document_id}: expected compatible raw and standardize_only, got "
                f"raw={compatibility.raw_compatible}, action={action}"
            )
        cache_rows.append({
            "document_id": document_id,
            "raw_compatible": compatibility.raw_compatible,
            "standardized_compatible": compatibility.standardized_compatible,
            "planned_action": action,
            "cloud_api_would_run": action == "run_adapter",
            "notes": "|".join(notes),
            "reasons": "|".join(compatibility.reasons),
        })

    old_results: list[ObjectEvaluationResult] = []
    new_results: list[ObjectEvaluationResult] = []
    inventory_rows: list[dict] = []
    chemistry_rows: list[dict] = []

    for document_id in DOCUMENTS:
        document_config = config.document_map()[document_id]
        pdf_path = resolve_document_path(root, document_config)
        cache_root = root / config.output_root / "cache" / "ocr_space" / document_id
        raw_result = RawToolResult.model_validate_json(
            (cache_root / "raw_result_snapshot.json").read_text(encoding="utf-8")
        )
        old_document = json.loads(
            (before_root / document_id / "normalized.json").read_text(encoding="utf-8")
        )
        old_evaluations = [
            ObjectEvaluationResult.model_validate(item)
            for item in json.loads(
                (before_root / document_id / "object_evaluation.json").read_text(encoding="utf-8")
            )
        ]
        old_results.extend(old_evaluations)

        document_output = output / document_id
        assets_dir = document_output / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        standardized = adapter.standardize(
            raw_result,
            cache_root / "raw",
            assets_dir,
            document_id=document_id,
            pdf_path=pdf_path,
        )
        standardized.raw_artifacts = sorted(set(raw_result.artifacts))
        normalized = normalize_document(standardized, runner.normalization_config)
        relevant_gt = [item for item in runner.ground_truth if item.document_id == document_id]
        matches = match_document(relevant_gt, normalized, config=runner.matching_config)
        evaluations = _pair_object_results(
            tool="ocr_space",
            document_id=document_id,
            matches=matches,
            framework=framework,
        )
        new_results.extend(evaluations)

        write_json(document_output / "standardized.json", standardized.model_dump(mode="json"))
        write_json(document_output / "normalized.json", normalized.model_dump(mode="json"))
        write_json(document_output / "matches.json", [item.model_dump(mode="json") for item in matches])
        write_json(
            document_output / "object_evaluation.json",
            [item.model_dump(mode="json") for item in evaluations],
        )

        old_pages = {page["page_number"]: page for page in old_document["pages"]}
        for page in normalized.pages:
            old_page = old_pages[page.page_number]
            for field in ("text_blocks", "tables", "formulas", "images", "diagrams"):
                current = [
                    _without_order_index(item.model_dump(mode="json"))
                    for item in getattr(page, field)
                ]
                previous = [_without_order_index(item) for item in old_page[field]]
                if current != previous:
                    raise AssertionError(f"{document_id} page {page.page_number}: {field} changed")

            for chemical in page.chemical_objects:
                inventory_rows.append({
                    "document_id": document_id,
                    "page_number": page.page_number,
                    "element_id": chemical.element_id,
                    "subtype": chemical.subtype,
                    "raw_formula": chemical.raw_formula,
                    "normalized_formula": chemical.normalized_formula,
                    "text_labels": json.dumps(chemical.text_labels, ensure_ascii=False),
                    "bbox": json.dumps(chemical.bbox.as_list if chemical.bbox else None),
                    "source": chemical.provenance.get("source"),
                    "asset_reference_kind": chemical.provenance.get("asset_reference_kind"),
                })

        old_by_id = {item.object_id: item for item in old_evaluations}
        new_by_id = {item.object_id: item for item in evaluations}
        match_by_id = {item.object_id: item for item in matches}
        for object_id, new in sorted(new_by_id.items()):
            old = old_by_id[object_id]
            if new.category != "chemistry":
                if old.model_dump(mode="json") != new.model_dump(mode="json"):
                    raise AssertionError(f"Non-chemistry evaluation changed: {object_id}")
                continue
            match = match_by_id[object_id]
            chemistry_rows.append({
                "object_id": object_id,
                "object_type": match.object_type,
                "old_matched": old.matched,
                "new_matched": new.matched,
                "old_score": old.object_score,
                "new_score": new.object_score,
                "score_delta": new.object_score - old.object_score,
                "match_method": match.match_method,
                "match_score": match.match_score,
                "prediction_element_id": match.prediction_element_id,
                "detection_score": 100.0 if new.matched else 0.0,
                "structured_extraction_score": _object_metric(
                    new, "structured_extraction"
                ),
            })

    if len(old_results) != 40 or len(new_results) != 40:
        raise AssertionError("Expected 40 old and 40 new OCR.Space object evaluations")

    _write_csv(output / "chemical_object_inventory.csv", inventory_rows)
    _write_csv(output / "chemistry_score_changes.csv", chemistry_rows)
    _write_csv(output / "cache_plan.csv", cache_rows)

    after_hashes = _hash_tree(protected_paths, root=root)
    if after_hashes != before_hashes:
        changed = sorted(set(before_hashes) | set(after_hashes))
        changed = [path for path in changed if before_hashes.get(path) != after_hashes.get(path)]
        raise AssertionError(f"Protected cached/official files changed: {changed}")

    old_chemistry = _aggregate_value(old_results, "chemistry_score", runner.scoring_config)
    new_chemistry = _aggregate_value(new_results, "chemistry_score", runner.scoring_config)
    old_detection = _aggregate_value(
        old_results, "chemistry_detection_score", runner.scoring_config
    )
    new_detection = _aggregate_value(
        new_results, "chemistry_detection_score", runner.scoring_config
    )
    old_structured_extraction = _aggregate_optional(
        old_results,
        "chemistry_structured_extraction_score",
        runner.scoring_config,
    )
    new_structured_extraction = _aggregate_optional(
        new_results,
        "chemistry_structured_extraction_score",
        runner.scoring_config,
    )
    old_overall = _aggregate_value(old_results, "overall_quality_score", runner.scoring_config)
    new_overall = _aggregate_value(new_results, "overall_quality_score", runner.scoring_config)
    _write_csv(
        output / "chemistry_detection_extraction.csv",
        [
            {
                "metric": "chemistry_detection_score",
                "before": old_detection,
                "after": new_detection,
                "denominator": "all_chemistry_gt_objects",
                "conditional_on_detection": False,
                "included_in_overall": False,
            },
            {
                "metric": "chemistry_structured_extraction_score",
                "before": old_structured_extraction,
                "after": new_structured_extraction,
                "denominator": "matched_chemistry_objects_only",
                "conditional_on_detection": True,
                "included_in_overall": False,
            },
        ],
    )
    summary = {
        "repair": "ocr_space_chemistry_objects_v1",
        "source_experiment": EXPERIMENT_ID,
        "comparison_baseline": BEFORE_REPAIR.as_posix(),
        "documents": len(DOCUMENTS),
        "chemical_objects": len(inventory_rows),
        "linear_formula_objects": sum(row["subtype"] == "linear_formula" for row in inventory_rows),
        "structure_objects": sum(row["subtype"] == "structure" for row in inventory_rows),
        "structure_objects_with_bbox": sum(
            row["subtype"] == "structure" and row["bbox"] != "null" for row in inventory_rows
        ),
        "structure_objects_with_remote_renderable_uri": sum(
            row["asset_reference_kind"] == "remote_renderable_formula_uri"
            for row in inventory_rows
        ),
        "chemistry_gt_objects": len(chemistry_rows),
        "old_chemistry_matched": sum(bool(row["old_matched"]) for row in chemistry_rows),
        "new_chemistry_matched": sum(bool(row["new_matched"]) for row in chemistry_rows),
        "old_chemistry_score": old_chemistry,
        "new_chemistry_score": new_chemistry,
        "chemistry_score_delta": new_chemistry - old_chemistry,
        "old_chemistry_detection_score": old_detection,
        "new_chemistry_detection_score": new_detection,
        "old_chemistry_structured_extraction_score": old_structured_extraction,
        "new_chemistry_structured_extraction_score": new_structured_extraction,
        "structured_extraction_is_conditional_on_detection": True,
        "split_reporting_changes_chemistry_or_overall": False,
        "old_overall_score": old_overall,
        "new_overall_score": new_overall,
        "overall_score_delta": new_overall - old_overall,
        "non_chemistry_standardized_content_unchanged": True,
        "non_chemistry_evaluations_unchanged": True,
        "protected_inputs_and_official_outputs_unchanged": True,
        "protected_file_count": len(before_hashes),
        "adapter_acquisitions": 0,
        "cloud_api_calls": 0,
        "cache_pairs_raw_compatible": sum(bool(row["raw_compatible"]) for row in cache_rows),
        "cache_pairs_planned_standardize_only": sum(
            row["planned_action"] == "standardize_only" for row in cache_rows
        ),
        "cache_pairs_would_call_cloud_api": sum(
            bool(row["cloud_api_would_run"]) for row in cache_rows
        ),
        "ground_truth_used_by_standardizer": False,
        "chemical_correction_applied": False,
        "official_experiment_changed": False,
        "remaining_repair_items": 0,
    }
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
