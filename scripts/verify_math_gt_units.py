from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from pdf_benchmark.benchmark.config import BenchmarkConfig
from pdf_benchmark.benchmark.ground_truth import (
    canonicalize_ground_truth,
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)
from pdf_benchmark.benchmark.matching import match_document
from pdf_benchmark.benchmark.runner import BenchmarkRunner, _pair_object_results
from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.models import StandardizedDocument
from pdf_benchmark.normalization.pipeline import normalize_document


TOOLS = (
    "pymupdf",
    "pdfplumber",
    "pdfminer",
    "docling",
    "mineru",
    "ocr_space",
    "nutrient",
    "mindee",
    "adobe_extract",
    "llamaparse",
)
TARGETS = {"D04": "MATH_005", "D05": "MATH_006"}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _pretty_object(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    return "\n".join("    " + line for line in raw.splitlines())


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    before_payload = json.loads(
        (args.output / "gt_before.json").read_text(encoding="utf-8")
    )
    patch = json.loads((args.output / "gt_patch.json").read_text(encoding="utf-8"))
    before_by_id = {item["object_id"]: item for item in before_payload["objects"]}

    json_path = root / "ground_truth/objects.json"
    jsonl_path = root / "ground_truth/objects.jsonl"
    json_objects = json.loads(json_path.read_text(encoding="utf-8"))["objects"]
    jsonl_objects = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if json_objects != jsonl_objects:
        raise AssertionError("objects.json and objects.jsonl differ")

    ground_truth = load_ground_truth(root / "ground_truth")
    manifest = load_object_manifest(root / "benchmark/object_manifest.json")
    validate_ground_truth_manifest(ground_truth, manifest)
    current_by_id = {item.object_id: item for item in ground_truth}

    expected_math_005 = patch["MATH_005"]["after"]
    expected_math_006 = patch["MATH_006"]["after"]
    raw_current = {item["object_id"]: item for item in json_objects}
    if raw_current["MATH_005"] != expected_math_005:
        raise AssertionError("MATH_005 differs from the reviewed patch")
    if raw_current["MATH_006"] != expected_math_006:
        raise AssertionError("MATH_006 differs from the reviewed patch")

    reconstructed_hashes: dict[str, str] = {}
    for relative_path in ("ground_truth/objects.json", "ground_truth/objects.jsonl"):
        path = root / relative_path
        text = path.read_text(encoding="utf-8")
        for object_id in ("MATH_005", "MATH_006"):
            if relative_path.endswith(".jsonl"):
                old = json.dumps(before_by_id[object_id], ensure_ascii=False)
                new = json.dumps(patch[object_id]["after"], ensure_ascii=False)
            else:
                old = _pretty_object(before_by_id[object_id])
                new = _pretty_object(patch[object_id]["after"])
            if text.count(new) != 1:
                raise AssertionError(f"{relative_path}: {object_id} patch is not unique")
            text = text.replace(new, old, 1)
        reconstructed = _sha256_bytes(text.encode("utf-8"))
        reconstructed_hashes[relative_path] = reconstructed
        if reconstructed != before_payload["before_sha256"][relative_path]:
            raise AssertionError(f"{relative_path}: changes exceed the two reviewed objects")

    manifest_hash = _sha256(root / "benchmark/object_manifest.json")
    if manifest_hash != before_payload["before_sha256"]["benchmark/object_manifest.json"]:
        raise AssertionError("object_manifest.json changed")

    config = BenchmarkConfig.from_yaml(root / "config/benchmark.yaml")
    runner = BenchmarkRunner(project_root=root, config=config)
    framework = EvaluationFramework(runner.scoring_config)
    score_rows: list[dict] = []

    for document_id, object_id in TARGETS.items():
        new_doc_gt = [
            item.model_copy(deep=True)
            for item in ground_truth
            if item.document_id == document_id
        ]
        old_doc_gt = [
            canonicalize_ground_truth(before_by_id[object_id])
            if item.object_id == object_id
            else item.model_copy(deep=True)
            for item in new_doc_gt
        ]
        for tool in TOOLS:
            standardized = StandardizedDocument.model_validate_json(
                (
                    root
                    / config.output_root
                    / "cache"
                    / tool
                    / document_id
                    / "standardized.json"
                ).read_text(encoding="utf-8")
            )
            normalized = normalize_document(standardized, runner.normalization_config)
            old_matches = {
                item.object_id: item
                for item in match_document(
                    old_doc_gt, normalized, config=runner.matching_config
                )
            }
            new_matches = {
                item.object_id: item
                for item in match_document(
                    new_doc_gt, normalized, config=runner.matching_config
                )
            }
            old_scores = {
                item.object_id: item
                for item in _pair_object_results(
                    tool=tool,
                    document_id=document_id,
                    matches=[old_matches[object_id]],
                    framework=framework,
                )
            }
            new_scores = {
                item.object_id: item
                for item in _pair_object_results(
                    tool=tool,
                    document_id=document_id,
                    matches=[new_matches[object_id]],
                    framework=framework,
                )
            }
            before_match = old_matches[object_id]
            after_match = new_matches[object_id]
            before_score = old_scores[object_id]
            after_score = new_scores[object_id]
            score_rows.append(
                {
                    "tool": tool,
                    "document_id": document_id,
                    "object_id": object_id,
                    "old_gt_matched": before_match.matched,
                    "corrected_gt_matched": after_match.matched,
                    "old_prediction_element_id": before_match.prediction_element_id,
                    "corrected_prediction_element_id": after_match.prediction_element_id,
                    "old_match_method": before_match.match_method,
                    "corrected_match_method": after_match.match_method,
                    "old_match_score": before_match.match_score,
                    "corrected_match_score": after_match.match_score,
                    "old_gt_score": before_score.object_score,
                    "corrected_gt_score": after_score.object_score,
                    "score_delta": after_score.object_score - before_score.object_score,
                }
            )

    _write_csv(args.output / "gt_score_changes.csv", score_rows)
    gt_diff = {
        object_id: {
            "decision": patch[object_id]["decision"],
            "before": patch[object_id]["before"],
            "after": patch[object_id]["after"],
        }
        for object_id in ("MATH_005", "MATH_006")
    }
    (args.output / "gt_diff.json").write_text(
        json.dumps(gt_diff, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    changed = [row for row in score_rows if abs(float(row["score_delta"])) > 1e-12]
    summary = {
        "repair": "math_gt_units_v1",
        "source_experiment": before_payload["source_experiment_id"],
        "ground_truth_objects_changed": ["MATH_005", "MATH_006"],
        "MATH_005_unit": "single numbered display equation (6)",
        "MATH_006_unit": "complete multiline numbered equation (57)",
        "json_jsonl_equal": True,
        "manifest_valid": True,
        "object_manifest_unchanged": True,
        "gt_files_reconstruct_original_sha": True,
        "reconstructed_sha256": reconstructed_hashes,
        "after_sha256": {
            "ground_truth/objects.json": _sha256(json_path),
            "ground_truth/objects.jsonl": _sha256(jsonl_path),
            "benchmark/object_manifest.json": manifest_hash,
        },
        "diagnostic_score_rows": len(score_rows),
        "diagnostic_score_rows_changed": len(changed),
        "diagnostic_scores_improved": sum(
            float(row["score_delta"]) > 1e-12 for row in score_rows
        ),
        "diagnostic_scores_worsened": sum(
            float(row["score_delta"]) < -1e-12 for row in score_rows
        ),
        "cloud_calls": 0,
        "parser_runs": 0,
        "official_experiment_changed": False,
        "remaining_repair_items": 2,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
