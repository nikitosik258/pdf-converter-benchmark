from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
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
DOCUMENTS = ("D01", "D02", "D03", "D04", "D05")
VISUAL_TYPES = {"image", "diagram"}


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


def _load_old_matcher(path: Path):
    name = "pdf_benchmark.benchmark._before_visual_detection_policy"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.match_document


def _score_map(tool, document_id, matches, framework):
    return {
        item.object_id: item
        for item in _pair_object_results(
            tool=tool,
            document_id=document_id,
            matches=matches,
            framework=framework,
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    before = json.loads((args.output / "before.json").read_text(encoding="utf-8"))
    bbox_patch = json.loads(
        (args.output / "chem_bbox_patch.json").read_text(encoding="utf-8")
    )

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
    current_raw = {item["object_id"]: item for item in json_objects}
    if current_raw["CHEM_001"] != bbox_patch["after"]:
        raise AssertionError("CHEM_001 differs from the reviewed patch")

    ground_truth = load_ground_truth(root / "ground_truth")
    manifest = load_object_manifest(root / "benchmark/object_manifest.json")
    validate_ground_truth_manifest(ground_truth, manifest)
    manifest_hash = _sha256(root / "benchmark/object_manifest.json")
    if manifest_hash != before["before_sha256"]["benchmark/object_manifest.json"]:
        raise AssertionError("object_manifest.json changed")

    reconstructed_hashes: dict[str, str] = {}
    for relative_path in ("ground_truth/objects.json", "ground_truth/objects.jsonl"):
        text = (root / relative_path).read_text(encoding="utf-8")
        if relative_path.endswith(".jsonl"):
            old = json.dumps(bbox_patch["before"], ensure_ascii=False)
            new = json.dumps(bbox_patch["after"], ensure_ascii=False)
        else:
            old = _pretty_object(bbox_patch["before"])
            new = _pretty_object(bbox_patch["after"])
        if text.count(new) != 1:
            raise AssertionError(f"{relative_path}: CHEM_001 patch is not unique")
        reconstructed = _sha256_bytes(text.replace(new, old, 1).encode("utf-8"))
        reconstructed_hashes[relative_path] = reconstructed
        if reconstructed != before["before_sha256"][relative_path]:
            raise AssertionError(f"{relative_path}: change exceeds CHEM_001 bbox")

    config = BenchmarkConfig.from_yaml(root / "config/benchmark.yaml")
    runner = BenchmarkRunner(project_root=root, config=config)
    framework = EvaluationFramework(runner.scoring_config)
    old_match_document = _load_old_matcher(
        args.output / "matching.before_visual_policy.py"
    )
    experiment = before["source_experiment_id"]
    experiment_manifest = json.loads(
        (
            root
            / "outputs/benchmark/experiments"
            / experiment
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )

    visual_rows: list[dict] = []
    for tool in TOOLS:
        run_id = experiment_manifest["tool_runs"][tool]
        for document_id in DOCUMENTS:
            pair = json.loads(
                (
                    root
                    / "outputs/benchmark/runs"
                    / run_id
                    / "pairs"
                    / tool
                    / document_id
                    / "pair_result.json"
                ).read_text(encoding="utf-8")
            )
            document = StandardizedDocument.model_validate_json(
                Path(pair["paths"]["normalized"]).read_text(encoding="utf-8")
            )
            relevant = [
                item
                for item in ground_truth
                if item.document_id == document_id and item.object_type in VISUAL_TYPES
            ]
            if not relevant:
                continue
            old_matches = old_match_document(relevant, document)
            new_matches = match_document(
                relevant, document, config=runner.matching_config
            )
            old_by_id = {item.object_id: item for item in old_matches}
            new_by_id = {item.object_id: item for item in new_matches}
            old_scores = _score_map(tool, document_id, old_matches, framework)
            new_scores = _score_map(tool, document_id, new_matches, framework)
            for object_id in old_by_id:
                old_match = old_by_id[object_id]
                new_match = new_by_id[object_id]
                if (
                    old_match.matched != new_match.matched
                    or old_match.prediction_element_id
                    != new_match.prediction_element_id
                    or old_match.match_method != new_match.match_method
                    or abs(old_match.match_score - new_match.match_score) > 1e-12
                ):
                    raise AssertionError(
                        f"Visual assignment changed: {tool}/{document_id}/{object_id}"
                    )
                if new_match.context.get("detection_policy") != "sampled_gt_recall_v1":
                    raise AssertionError("New visual policy missing")
                if "detection_fp" in new_match.context:
                    raise AssertionError("Sampled GT context still labels candidates FP")
                old_score = old_scores[object_id].object_score
                new_score = new_scores[object_id].object_score
                visual_rows.append(
                    {
                        "tool": tool,
                        "document_id": document_id,
                        "object_id": object_id,
                        "object_type": old_match.object_type,
                        "matched": old_match.matched,
                        "prediction_element_id": old_match.prediction_element_id,
                        "legacy_detection_tp": old_match.context.get("detection_tp"),
                        "legacy_detection_fp": old_match.context.get("detection_fp"),
                        "legacy_detection_fn": old_match.context.get("detection_fn"),
                        "sampled_unmatched_candidate_count": new_match.context.get(
                            "unmatched_candidate_count"
                        ),
                        "old_score": old_score,
                        "new_score": new_score,
                        "score_delta": new_score - old_score,
                    }
                )

    chem_rows: list[dict] = []
    old_chem_001 = canonicalize_ground_truth(bbox_patch["before"])
    new_d02_gt = [item for item in ground_truth if item.document_id == "D02"]
    old_d02_gt = [
        old_chem_001 if item.object_id == "CHEM_001" else item.model_copy(deep=True)
        for item in new_d02_gt
    ]
    for tool in TOOLS:
        run_id = experiment_manifest["tool_runs"][tool]
        pair = json.loads(
            (
                root
                / "outputs/benchmark/runs"
                / run_id
                / "pairs"
                / tool
                / "D02"
                / "pair_result.json"
            ).read_text(encoding="utf-8")
        )
        document = StandardizedDocument.model_validate_json(
            Path(pair["paths"]["normalized"]).read_text(encoding="utf-8")
        )
        old_matches = old_match_document(old_d02_gt, document)
        new_matches = match_document(new_d02_gt, document, config=runner.matching_config)
        old_scores = _score_map(tool, "D02", old_matches, framework)
        new_scores = _score_map(tool, "D02", new_matches, framework)
        before_score = old_scores["CHEM_001"]
        after_score = new_scores["CHEM_001"]
        chem_rows.append(
            {
                "tool": tool,
                "old_matched": before_score.matched,
                "new_matched": after_score.matched,
                "old_score": before_score.object_score,
                "new_score": after_score.object_score,
                "score_delta": after_score.object_score - before_score.object_score,
            }
        )

    _write_csv(args.output / "visual_score_changes.csv", visual_rows)
    _write_csv(args.output / "chem_001_score_changes.csv", chem_rows)
    changed_visual = [
        row for row in visual_rows if abs(float(row["score_delta"])) > 1e-12
    ]
    summary = {
        "repair": "chem_bbox_visual_detection_v1",
        "source_experiment": experiment,
        "CHEM_001_bbox_before": bbox_patch["before"]["bbox"],
        "CHEM_001_bbox_after": bbox_patch["after"]["bbox"],
        "CHEM_001_score_rows": len(chem_rows),
        "CHEM_001_score_rows_changed": sum(
            abs(float(row["score_delta"])) > 1e-12 for row in chem_rows
        ),
        "visual_detection_policy_before": "legacy_exhaustive_page_f1",
        "visual_detection_policy_after": "sampled_gt_recall_v1",
        "visual_rows": len(visual_rows),
        "visual_assignments_unchanged": True,
        "legacy_rows_with_fp": sum(
            int(row["legacy_detection_fp"] or 0) > 0 for row in visual_rows
        ),
        "new_rows_with_detection_fp_field": 0,
        "visual_score_rows_changed": len(changed_visual),
        "visual_scores_improved": sum(
            float(row["score_delta"]) > 1e-12 for row in visual_rows
        ),
        "visual_scores_worsened": sum(
            float(row["score_delta"]) < -1e-12 for row in visual_rows
        ),
        "json_jsonl_equal": True,
        "manifest_valid": True,
        "object_manifest_unchanged": True,
        "gt_files_reconstruct_original_sha": True,
        "reconstructed_sha256": reconstructed_hashes,
        "after_sha256": {
            path: _sha256(root / path)
            for path in before["before_sha256"]
        },
        "cloud_calls": 0,
        "parser_runs": 0,
        "official_experiment_changed": False,
        "newly_confirmed_unrepaired": [
            "CHEM_002 bbox clips the Fe prefix; GT unchanged pending explicit approval"
        ],
        "remaining_repair_items": 1,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
