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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _pretty_object(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    return "\n".join("    " + line for line in raw.splitlines())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    before = json.loads((args.output / "before.json").read_text(encoding="utf-8"))
    patch = json.loads((args.output / "gt_patch.json").read_text(encoding="utf-8"))

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
    current = {item["object_id"]: item for item in json_objects}
    if current["CHEM_002"] != patch["after"]:
        raise AssertionError("CHEM_002 differs from the reviewed patch")

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
            old = json.dumps(patch["before"], ensure_ascii=False)
            new = json.dumps(patch["after"], ensure_ascii=False)
        else:
            old = _pretty_object(patch["before"])
            new = _pretty_object(patch["after"])
        if text.count(new) != 1:
            raise AssertionError(f"{relative_path}: CHEM_002 patch is not unique")
        reconstructed = _sha256_bytes(text.replace(new, old, 1).encode("utf-8"))
        reconstructed_hashes[relative_path] = reconstructed
        if reconstructed != before["before_sha256"][relative_path]:
            raise AssertionError(f"{relative_path}: change exceeds CHEM_002 bbox")

    config = BenchmarkConfig.from_yaml(root / "config/benchmark.yaml")
    runner = BenchmarkRunner(project_root=root, config=config)
    framework = EvaluationFramework(runner.scoring_config)
    experiment = before["source_experiment_id"]
    experiment_manifest = json.loads(
        (
            root
            / "outputs/benchmark/experiments"
            / experiment
            / "experiment_manifest.json"
        ).read_text(encoding="utf-8")
    )
    new_d02_gt = [item for item in ground_truth if item.document_id == "D02"]
    old_chem_002 = canonicalize_ground_truth(patch["before"])
    old_d02_gt = [
        old_chem_002 if item.object_id == "CHEM_002" else item.model_copy(deep=True)
        for item in new_d02_gt
    ]

    score_rows: list[dict] = []
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
        old_matches = match_document(
            old_d02_gt, document, config=runner.matching_config
        )
        new_matches = match_document(
            new_d02_gt, document, config=runner.matching_config
        )
        old_match = {item.object_id: item for item in old_matches}["CHEM_002"]
        new_match = {item.object_id: item for item in new_matches}["CHEM_002"]
        old_scores = {
            item.object_id: item
            for item in _pair_object_results(
                tool=tool,
                document_id="D02",
                matches=[old_match],
                framework=framework,
            )
        }
        new_scores = {
            item.object_id: item
            for item in _pair_object_results(
                tool=tool,
                document_id="D02",
                matches=[new_match],
                framework=framework,
            )
        }
        old_result = old_scores["CHEM_002"]
        new_result = new_scores["CHEM_002"]
        score_rows.append(
            {
                "tool": tool,
                "old_matched": old_result.matched,
                "new_matched": new_result.matched,
                "old_score": old_result.object_score,
                "new_score": new_result.object_score,
                "score_delta": new_result.object_score - old_result.object_score,
            }
        )

    with (args.output / "score_changes.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(score_rows[0]))
        writer.writeheader()
        writer.writerows(score_rows)

    summary = {
        "repair": "chem002_bbox_v1",
        "source_experiment": experiment,
        "bbox_before": patch["before"]["bbox"],
        "bbox_after": patch["after"]["bbox"],
        "score_rows": len(score_rows),
        "score_rows_changed": sum(
            abs(float(row["score_delta"])) > 1e-12 for row in score_rows
        ),
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
        "cloud_calls": 0,
        "parser_runs": 0,
        "official_experiment_changed": False,
        "remaining_repair_items": 0,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
