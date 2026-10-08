"""Re-standardize cached OCR.Space raw responses and verify text-block repairs offline."""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

from pdf_benchmark.adapters.cloud import OCRSpaceAdapter
from pdf_benchmark.benchmark.cache import (
    PairCache,
    cache_compatibility,
    sha256_file,
    sha256_json,
)
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


def _without_order_index(payload: dict) -> dict:
    result = dict(payload)
    result.pop("order_index", None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/repairs/ocr_space_text_blocks_v1"),
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty repair directory: {output}")
    output.mkdir(parents=True, exist_ok=True)

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

    protected_paths = [experiment_dir]
    for document_id in DOCUMENTS:
        cache_root = root / config.output_root / "cache" / "ocr_space" / document_id
        protected_paths.extend([cache_root / "raw", cache_root / "raw_result_snapshot.json"])
        pair_path = (
            root / config.output_root / "runs" / run_id / "pairs" / "ocr_space"
            / document_id / "pair_result.json"
        )
        pair = json.loads(pair_path.read_text(encoding="utf-8"))
        protected_paths.append(Path(pair["cache_dir"]))
    before_hashes = _hash_tree(protected_paths, root=root)

    cache_rows: list[dict] = []
    for document_config in config.documents:
        document_id = document_config.document_id
        pdf_path = resolve_document_path(root, document_config)
        cache = PairCache(root / config.output_root / "cache" / "ocr_space" / document_id)
        source_sha256 = sha256_file(pdf_path)
        tool_config_sha256 = sha256_json(tool_config)
        fingerprints = runner._stage_fingerprints(
            tool="ocr_space",
            source_sha256=source_sha256,
            tool_config_sha256=tool_config_sha256,
        )
        compatibility = cache_compatibility(
            cache,
            source_sha256=source_sha256,
            tool_config_sha256=tool_config_sha256,
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
    comparison_rows: list[dict] = []
    document_rows: list[dict] = []

    for document_id in DOCUMENTS:
        document_config = config.document_map()[document_id]
        pdf_path = resolve_document_path(root, document_config)
        cache_root = root / config.output_root / "cache" / "ocr_space" / document_id
        raw_dir = cache_root / "raw"
        raw_result = RawToolResult.model_validate_json(
            (cache_root / "raw_result_snapshot.json").read_text(encoding="utf-8")
        )
        pair_path = (
            root / config.output_root / "runs" / run_id / "pairs" / "ocr_space"
            / document_id / "pair_result.json"
        )
        pair = json.loads(pair_path.read_text(encoding="utf-8"))
        old_document = json.loads(Path(pair["paths"]["normalized"]).read_text(encoding="utf-8"))
        old_evaluations = [
            ObjectEvaluationResult.model_validate(item)
            for item in json.loads(
                Path(pair["paths"]["object_evaluation"]).read_text(encoding="utf-8")
            )
        ]
        old_results.extend(old_evaluations)

        document_output = output / document_id
        assets_dir = document_output / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        standardized = adapter.standardize(
            raw_result,
            raw_dir,
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

        old_by_id = {item.object_id: item for item in old_evaluations}
        new_by_id = {item.object_id: item for item in evaluations}
        match_by_id = {item.object_id: item for item in matches}
        for object_id in sorted(new_by_id):
            if new_by_id[object_id].category != "text":
                continue
            old = old_by_id[object_id]
            new = new_by_id[object_id]
            match = match_by_id[object_id]
            comparison_rows.append({
                "document_id": document_id,
                "object_id": object_id,
                "old_matched": old.matched,
                "new_matched": new.matched,
                "old_score": old.object_score,
                "new_score": new.object_score,
                "score_delta": new.object_score - old.object_score,
                "new_match_method": match.match_method,
                "new_match_score": match.match_score,
                "new_prediction_element_id": match.prediction_element_id,
                "new_source_block_count": match.context.get("source_block_count"),
            })

        old_pages = {page["page_number"]: page for page in old_document["pages"]}
        old_text_count = sum(len(page["text_blocks"]) for page in old_document["pages"])
        new_blocks = [block for page in normalized.pages for block in page.text_blocks]
        sources = collections.Counter(block.provenance.get("source") for block in new_blocks)
        for page in normalized.pages:
            old_page = old_pages[page.page_number]
            if [
                _without_order_index(table.model_dump(mode="json"))
                for table in page.tables
            ] != [_without_order_index(table) for table in old_page["tables"]]:
                raise AssertionError(f"{document_id} page {page.page_number}: tables changed")
            for field in ("formulas", "chemical_objects", "images", "diagrams"):
                new_payload = [
                    _without_order_index(item.model_dump(mode="json"))
                    for item in getattr(page, field)
                ]
                if new_payload != [_without_order_index(item) for item in old_page[field]]:
                    raise AssertionError(f"{document_id} page {page.page_number}: {field} changed")
        document_rows.append({
            "document_id": document_id,
            "pages": len(normalized.pages),
            "old_text_blocks": old_text_count,
            "new_text_blocks": len(new_blocks),
            "spatial_overlay_blocks": sources.get("TextOverlay.Lines", 0),
            "fallback_segment_blocks": sources.get("ParsedText.segment", 0),
            "blocks_with_bbox": sum(block.bbox is not None for block in new_blocks),
            "max_block_chars": max((len(block.raw_text) for block in new_blocks), default=0),
            "old_text_matched": sum(item.matched for item in old_evaluations if item.category == "text"),
            "new_text_matched": sum(item.matched for item in evaluations if item.category == "text"),
        })

    if len(old_results) != 40 or len(new_results) != 40:
        raise AssertionError("Expected 40 old and 40 new OCR.Space object evaluations")

    old_text_score = _aggregate_value(old_results, "text_score", runner.scoring_config)
    new_text_score = _aggregate_value(new_results, "text_score", runner.scoring_config)
    old_overall = _aggregate_value(old_results, "overall_quality_score", runner.scoring_config)
    new_overall = _aggregate_value(new_results, "overall_quality_score", runner.scoring_config)

    _write_csv(output / "text_object_changes.csv", comparison_rows)
    _write_csv(output / "document_block_counts.csv", document_rows)
    _write_csv(output / "cache_plan.csv", cache_rows)

    after_hashes = _hash_tree(protected_paths, root=root)
    if after_hashes != before_hashes:
        changed = sorted(set(before_hashes) | set(after_hashes))
        changed = [path for path in changed if before_hashes.get(path) != after_hashes.get(path)]
        raise AssertionError(f"Protected cached/official files changed: {changed}")

    summary = {
        "repair": "ocr_space_text_blocks_v1",
        "source_experiment": EXPERIMENT_ID,
        "documents": len(DOCUMENTS),
        "pages": sum(int(row["pages"]) for row in document_rows),
        "old_text_blocks": sum(int(row["old_text_blocks"]) for row in document_rows),
        "new_text_blocks": sum(int(row["new_text_blocks"]) for row in document_rows),
        "spatial_overlay_blocks": sum(int(row["spatial_overlay_blocks"]) for row in document_rows),
        "fallback_segment_blocks": sum(int(row["fallback_segment_blocks"]) for row in document_rows),
        "blocks_with_bbox": sum(int(row["blocks_with_bbox"]) for row in document_rows),
        "old_text_matched": sum(bool(row["old_matched"]) for row in comparison_rows),
        "new_text_matched": sum(bool(row["new_matched"]) for row in comparison_rows),
        "old_text_score": old_text_score,
        "new_text_score": new_text_score,
        "text_score_delta": new_text_score - old_text_score,
        "old_overall_score": old_overall,
        "new_overall_score": new_overall,
        "overall_score_delta": new_overall - old_overall,
        "non_text_standardized_content_unchanged": True,
        "non_text_order_indexes_recomputed_after_text_segmentation": True,
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
    }
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
