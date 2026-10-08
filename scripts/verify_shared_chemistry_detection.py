"""Verify shared GT-independent chemistry enrichment from cached raw outputs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from pdf_benchmark.benchmark.cache import PairCache, cache_compatibility, sha256_file, sha256_json
from pdf_benchmark.benchmark.config import BenchmarkConfig, resolve_document_path
from pdf_benchmark.benchmark.matching import match_document
from pdf_benchmark.benchmark.registry import TOOL_SPECS
from pdf_benchmark.benchmark.runner import BenchmarkRunner, _pair_object_results, _tool_config
from pdf_benchmark.benchmark.worker import _rebase_raw_result, create_adapter
from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.evaluation.aggregation import aggregate_tool_scores
from pdf_benchmark.evaluation.models import ObjectEvaluationResult
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.normalization.pipeline import normalize_document
from pdf_benchmark.standardization import enrich_chemical_objects, linear_formula_candidates


SOURCE_EXPERIMENT = "20261003T202925Z_s42_ee4d6ac4"
TOOLS = (
    "pymupdf", "pdfplumber", "docling", "pdfminer", "mineru",
    "ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse",
)
DOCUMENTS = ("D01", "D02", "D03", "D04", "D05")
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".html", ".xml", ".csv"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _raw_evidence(raw_dir: Path) -> dict[str, Any]:
    files = [path for path in raw_dir.rglob("*") if path.is_file()]
    structure_files: list[str] = []
    formula_files: list[str] = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 50_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        folded = " ".join(text.casefold().split())
        if any(marker in folded for marker in (
            "структурная формула", "structural formula", "structure:", "\\chem{",
        )):
            structure_files.append(path.relative_to(raw_dir).as_posix())
        if linear_formula_candidates(text):
            formula_files.append(path.relative_to(raw_dir).as_posix())
    return {
        "raw_file_count": len(files),
        "raw_text_file_count": sum(path.suffix.lower() in TEXT_SUFFIXES for path in files),
        "raw_image_file_count": sum(path.suffix.lower() in IMAGE_SUFFIXES for path in files),
        "structure_evidence_files": "|".join(structure_files[:10]),
        "formula_evidence_files": "|".join(formula_files[:10]),
    }


def _score_map(results: list[ObjectEvaluationResult], runner: BenchmarkRunner) -> dict[str, float]:
    return {
        record.score_name: record.score
        for record in aggregate_tool_scores(results, config=runner.scoring_config)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/repairs/shared_chemistry_detection_v1"),
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
    experiment_dir = root / config.output_root / "experiments" / SOURCE_EXPERIMENT
    manifest = json.loads((experiment_dir / "experiment_manifest.json").read_text(encoding="utf-8"))
    baseline_rows = {
        row["tool"]: row
        for row in _read_csv(experiment_dir / "results" / "clean_results_dataset.csv")
    }

    protected = [
        experiment_dir,
        root / config.output_root / "latest_experiment.txt",
        root / "documents",
        root / "ground_truth",
        root / "benchmark" / "object_manifest.json",
    ]
    for tool in TOOLS:
        protected.append(root / config.output_root / "runs" / manifest["tool_runs"][tool])
        for document_id in DOCUMENTS:
            cache_root = root / config.output_root / "cache" / tool / document_id
            protected.extend([cache_root / "raw", cache_root / "raw_result_snapshot.json"])
    before_hashes = _hash_tree(protected, root=root)

    cache_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    inventory_rows: list[dict[str, Any]] = []
    object_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    all_results: dict[str, list[ObjectEvaluationResult]] = {tool: [] for tool in TOOLS}
    non_chemistry_unchanged = True

    for tool in TOOLS:
        tool_config, _ = _tool_config(root, tool)
        adapter = create_adapter(tool, tool_config)
        run_root = root / config.output_root / "runs" / manifest["tool_runs"][tool]
        for document_config in config.documents:
            document_id = document_config.document_id
            pdf_path = resolve_document_path(root, document_config)
            cache_root = root / config.output_root / "cache" / tool / document_id
            cache = PairCache(cache_root)
            fingerprints = runner._stage_fingerprints(
                tool=tool,
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
                spec=TOOL_SPECS[tool],
                raw_exists=cache.raw_snapshot.exists(),
                standardized_exists=cache.standardized.exists(),
                raw_compatible=compatibility.raw_compatible,
                standardized_compatible=compatibility.standardized_compatible,
                incompatibility_reasons=compatibility.reasons,
            )
            if not compatibility.raw_compatible or action != "standardize_only":
                raise AssertionError(
                    f"{tool}/{document_id}: expected raw-compatible standardize_only; "
                    f"got raw={compatibility.raw_compatible}, action={action}"
                )
            cache_rows.append({
                "tool": tool,
                "document_id": document_id,
                "raw_compatible": compatibility.raw_compatible,
                "standardized_compatible": compatibility.standardized_compatible,
                "planned_action": action,
                "adapter_acquisition_would_run": action == "run_adapter",
                "cloud_api_would_run": action == "run_adapter" and TOOL_SPECS[tool].kind == "cloud",
                "notes": "|".join(notes),
                "reasons": "|".join(compatibility.reasons),
            })

            pair = json.loads(
                (run_root / "pairs" / tool / document_id / "pair_result.json").read_text(encoding="utf-8")
            )
            old_evaluations = [
                ObjectEvaluationResult.model_validate(item)
                for item in json.loads(Path(pair["paths"]["object_evaluation"]).read_text(encoding="utf-8"))
            ]
            raw_result = RawToolResult.model_validate_json(cache.raw_snapshot.read_text(encoding="utf-8"))
            _rebase_raw_result(raw_result, cache)
            assets_dir = output / "assets" / tool / document_id
            assets_dir.mkdir(parents=True, exist_ok=True)
            standardized = adapter.standardize(
                raw_result,
                cache.raw_dir,
                assets_dir,
                document_id=document_id,
                pdf_path=pdf_path,
            )
            standardized = enrich_chemical_objects(
                standardized,
                raw_dir=cache.raw_dir,
                assets_dir=assets_dir,
            )
            normalized = normalize_document(standardized, runner.normalization_config)
            matches = match_document(
                [item for item in runner.ground_truth if item.document_id == document_id],
                normalized,
                config=runner.matching_config,
            )
            evaluations = _pair_object_results(
                tool=tool,
                document_id=document_id,
                matches=matches,
                framework=framework,
            )
            all_results[tool].extend(evaluations)

            old_by_id = {item.object_id: item for item in old_evaluations}
            for current in evaluations:
                old = old_by_id[current.object_id]
                stable_evaluation = (
                    current.category == old.category
                    and current.subtype == old.subtype
                    and current.matched == old.matched
                    and current.object_score == old.object_score
                    and current.metrics == old.metrics
                )
                if current.category != "chemistry" and not stable_evaluation:
                    non_chemistry_unchanged = False
                    raise AssertionError(
                        f"Non-chemistry evaluation changed: {tool}/{document_id}/{current.object_id}: "
                        f"old={old.model_dump(mode='json')!r}; current={current.model_dump(mode='json')!r}"
                    )
                if current.category == "chemistry":
                    object_rows.append({
                        "tool": tool,
                        "document_id": document_id,
                        "object_id": current.object_id,
                        "subtype": current.subtype,
                        "before_matched": old.matched,
                        "after_matched": current.matched,
                        "before_score": old.object_score,
                        "after_score": current.object_score,
                        "score_delta": current.object_score - old.object_score,
                    })

            for page in normalized.pages:
                for chemical in page.chemical_objects:
                    inventory_rows.append({
                        "tool": tool,
                        "document_id": document_id,
                        "page": page.page_number,
                        "element_id": chemical.element_id,
                        "subtype": chemical.subtype,
                        "raw_formula": chemical.raw_formula,
                        "normalized_formula": chemical.normalized_formula,
                        "text_labels": json.dumps(chemical.text_labels, ensure_ascii=False),
                        "bbox": json.dumps(chemical.bbox.as_list if chemical.bbox else None),
                        "asset_path": chemical.asset_path,
                        "source": chemical.provenance.get("source"),
                        "detector_version": chemical.provenance.get("detector_version"),
                        "gt_used": chemical.provenance.get("gt_used", False),
                    })

            if document_id == "D02":
                sources = sorted({
                    item.provenance.get("source", "")
                    for page in normalized.pages for item in page.chemical_objects
                })
                raw_rows.append({
                    "tool": tool,
                    "document_id": document_id,
                    **_raw_evidence(cache.raw_dir),
                    "standardized_tables": sum(len(page.tables) for page in normalized.pages),
                    "standardized_images": sum(len(page.images) for page in normalized.pages),
                    "chemistry_sources": "|".join(sources),
                })

    for tool in TOOLS:
        scores = _score_map(all_results[tool], runner)
        baseline = baseline_rows[tool]
        score_rows.append({
            "tool": tool,
            "before_chemistry_score": float(baseline["chemistry_score"]),
            "after_chemistry_score": scores["chemistry_score"],
            "before_detection_score": float(baseline["chemistry_detection_score"]),
            "after_detection_score": scores["chemistry_detection_score"],
            "before_structured_extraction_score": baseline["chemistry_structured_extraction_score"] or None,
            "after_structured_extraction_score": scores.get("chemistry_structured_extraction_score"),
            "before_overall_score": float(baseline["overall_score"]),
            "after_overall_score": scores["overall_quality_score"],
        })

    _write_csv(output / "cache_plan.csv", cache_rows)
    _write_csv(output / "raw_support_inventory.csv", raw_rows)
    _write_csv(output / "chemical_object_inventory.csv", inventory_rows)
    _write_csv(output / "chemistry_object_results.csv", object_rows)
    _write_csv(output / "chemistry_score_comparison.csv", score_rows)

    after_hashes = _hash_tree(protected, root=root)
    if after_hashes != before_hashes:
        changed = sorted(path for path in set(before_hashes) | set(after_hashes) if before_hashes.get(path) != after_hashes.get(path))
        raise AssertionError(f"Protected inputs/caches/official outputs changed: {changed}")

    allowed_sources = {
        "text_block", "spatial_text_line", "math_formula", "table_cell",
        "semantic_structural_formula_table", "image_under_structural_formula_header",
        "spatial_structure_labels_under_explicit_header", "parsed_text_strict_formula_syntax",
        "markdown_codecogs_chem_structure",
    }
    common_rows = [row for row in inventory_rows if row["detector_version"]]
    all_detected = all(
        sum(row["after_matched"] for row in object_rows if row["tool"] == tool) == 6
        for tool in TOOLS
    )
    summary = {
        "repair": "shared_chemistry_detection_v1",
        "source_experiment": SOURCE_EXPERIMENT,
        "tools": len(TOOLS),
        "documents": len(DOCUMENTS),
        "pairs": len(cache_rows),
        "chemical_objects": len(inventory_rows),
        "linear_formula_objects": sum(row["subtype"] == "linear_formula" for row in inventory_rows),
        "structure_objects": sum(row["subtype"] == "structure" for row in inventory_rows),
        "chemistry_gt_evaluations": len(object_rows),
        "tools_detecting_all_six_gt_objects": sum(
            sum(row["after_matched"] for row in object_rows if row["tool"] == tool) == 6
            for tool in TOOLS
        ),
        "all_tools_detect_six_of_six": all_detected,
        "non_chemistry_evaluations_unchanged": non_chemistry_unchanged,
        "ground_truth_used_by_enrichment": any(bool(row["gt_used"]) for row in common_rows),
        "generic_visual_reinterpretation_used": any(row["source"] not in allowed_sources for row in common_rows),
        "raw_support_rows": len(raw_rows),
        "cache_pairs_raw_compatible": sum(bool(row["raw_compatible"]) for row in cache_rows),
        "cache_pairs_planned_standardize_only": sum(row["planned_action"] == "standardize_only" for row in cache_rows),
        "adapter_acquisitions": sum(bool(row["adapter_acquisition_would_run"]) for row in cache_rows),
        "cloud_api_calls": sum(bool(row["cloud_api_would_run"]) for row in cache_rows),
        "protected_inputs_caches_official_outputs_unchanged": True,
        "protected_file_count": len(before_hashes),
        "official_experiment_changed": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
