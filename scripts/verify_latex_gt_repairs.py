from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from pdf_benchmark.benchmark.config import BenchmarkConfig
from pdf_benchmark.benchmark.ground_truth import (
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)
from pdf_benchmark.benchmark.matching import match_document
from pdf_benchmark.benchmark.runner import BenchmarkRunner, _pair_object_results
from pdf_benchmark.evaluation import EvaluationFramework
from pdf_benchmark.models import StandardizedDocument
from pdf_benchmark.normalization.latex import normalize_latex
from pdf_benchmark.normalization.pipeline import normalize_document


TOOLS = [
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
]
DOCUMENTS = ["D01", "D02", "D03", "D04", "D05"]
CORRECTED_IDS = ("MATH_002", "MATH_003", "MATH_004")
OLD_PREFIX = r"{}_{0}^{t}D"
NEW_PREFIX = r"{}_{t}D"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _score_map(results):
    return {result.object_id: result for result in results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    config = BenchmarkConfig.from_yaml(project_root / "config/benchmark.yaml")
    runner = BenchmarkRunner(project_root=project_root, config=config)

    formula_rows: list[dict] = []
    non_idempotent: list[dict] = []
    for tool in TOOLS:
        for document_id in DOCUMENTS:
            path = (
                project_root
                / config.output_root
                / "cache"
                / tool
                / document_id
                / "standardized.json"
            )
            document = StandardizedDocument.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            for page in document.pages:
                for formula in page.formulas:
                    once = normalize_latex(formula.latex)
                    twice = normalize_latex(once)
                    row = {
                        "tool": tool,
                        "document_id": document_id,
                        "element_id": formula.element_id,
                        "page": formula.page_number,
                        "source": formula.latex,
                        "normalized_once": once,
                        "normalized_twice": twice,
                        "idempotent": once == twice,
                    }
                    formula_rows.append(row)
                    if once != twice:
                        non_idempotent.append(row)

    json_root = json.loads(
        (project_root / "ground_truth/objects.json").read_text(encoding="utf-8")
    )
    json_objects = json_root["objects"]
    jsonl_objects = [
        json.loads(line)
        for line in (project_root / "ground_truth/objects.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    if json_objects != jsonl_objects:
        raise AssertionError("objects.json and objects.jsonl differ")

    ground_truth = load_ground_truth(project_root / config.ground_truth_dir)
    object_manifest = load_object_manifest(project_root / config.object_manifest)
    validate_ground_truth_manifest(ground_truth, object_manifest)
    gt_by_id = {item.object_id: item for item in ground_truth}
    expected_prefixes = {
        "MATH_002": r"{}_{t}D_*^{\alpha}",
        "MATH_003": r"{}_{t}D^{\alpha}",
        "MATH_004": r"{}_{t}D_*^{\alpha}",
    }
    for object_id, prefix in expected_prefixes.items():
        latex = gt_by_id[object_id].reference["latex"]
        if not latex.startswith(prefix) or OLD_PREFIX in latex:
            raise AssertionError(f"{object_id}: unexpected corrected reference {latex!r}")

    d04_gt = [item.model_copy(deep=True) for item in ground_truth if item.document_id == "D04"]
    old_d04_gt = [item.model_copy(deep=True) for item in d04_gt]
    for item in old_d04_gt:
        if item.object_id in CORRECTED_IDS:
            item.reference["latex"] = item.reference["latex"].replace(
                NEW_PREFIX,
                OLD_PREFIX,
                1,
            )

    framework = EvaluationFramework(runner.scoring_config)
    score_rows: list[dict] = []
    for tool in TOOLS:
        standardized = StandardizedDocument.model_validate_json(
            (
                project_root
                / config.output_root
                / "cache"
                / tool
                / "D04"
                / "standardized.json"
            ).read_text(encoding="utf-8")
        )
        normalized = normalize_document(standardized, runner.normalization_config)
        fixed_results = _score_map(
            _pair_object_results(
                tool=tool,
                document_id="D04",
                matches=match_document(d04_gt, normalized, config=runner.matching_config),
                framework=framework,
            )
        )
        old_results = _score_map(
            _pair_object_results(
                tool=tool,
                document_id="D04",
                matches=match_document(old_d04_gt, normalized, config=runner.matching_config),
                framework=framework,
            )
        )
        for object_id in CORRECTED_IDS:
            before = old_results[object_id]
            after = fixed_results[object_id]
            score_rows.append(
                {
                    "tool": tool,
                    "object_id": object_id,
                    "old_gt_matched": before.matched,
                    "corrected_gt_matched": after.matched,
                    "old_gt_score": before.object_score,
                    "corrected_gt_score": after.object_score,
                    "score_delta": after.object_score - before.object_score,
                }
            )

    before_payload = json.loads(
        (args.output / "gt_before.json").read_text(encoding="utf-8")
    )
    before_objects = {item["object_id"]: item for item in before_payload["objects"]}
    after_objects = {item["object_id"]: item for item in json_objects}
    gt_diff = []
    for object_id in CORRECTED_IDS:
        before = before_objects[object_id]
        after = after_objects[object_id]
        expected = json.loads(json.dumps(before))
        expected["reference"]["latex"] = expected["reference"]["latex"].replace(
            OLD_PREFIX,
            NEW_PREFIX,
            1,
        )
        if after != expected:
            raise AssertionError(f"{object_id}: more than the approved LaTeX prefix changed")
        gt_diff.append(
            {
                "object_id": object_id,
                "before_latex": before["reference"]["latex"],
                "after_latex": after["reference"]["latex"],
                "bbox_unchanged": before["bbox"] == after["bbox"],
                "evidence": (
                    "reports/code_audit/20261001T200058Z_s42_textdedup/"
                    f"ground_truth_checks/{object_id}.png"
                ),
            }
        )

    after_sha256 = {
        "ground_truth/objects.json": _sha256(project_root / "ground_truth/objects.json"),
        "ground_truth/objects.jsonl": _sha256(project_root / "ground_truth/objects.jsonl"),
        "benchmark/object_manifest.json": _sha256(
            project_root / "benchmark/object_manifest.json"
        ),
    }
    if (
        after_sha256["benchmark/object_manifest.json"]
        != before_payload["before_sha256"]["benchmark/object_manifest.json"]
    ):
        raise AssertionError("object_manifest.json changed")
    for relative_path in ("ground_truth/objects.json", "ground_truth/objects.jsonl"):
        current = (project_root / relative_path).read_bytes()
        old_bytes = OLD_PREFIX.encode("ascii")
        new_bytes = NEW_PREFIX.encode("ascii")
        if current.count(new_bytes) != 3:
            raise AssertionError(f"{relative_path}: expected exactly three corrected prefixes")
        reconstructed_sha256 = hashlib.sha256(
            current.replace(new_bytes, old_bytes)
        ).hexdigest()
        if reconstructed_sha256 != before_payload["before_sha256"][relative_path]:
            raise AssertionError(f"{relative_path}: changes exceed the three approved prefixes")

    _write_csv(args.output / "formula_idempotence.csv", formula_rows)
    _write_csv(args.output / "gt_score_changes.csv", score_rows)
    (args.output / "gt_diff.json").write_text(
        json.dumps(gt_diff, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        "repair": "latex_idempotence_gt_indices_v1",
        "formulas_checked": len(formula_rows),
        "formulas_with_latex": sum(bool(row["source"]) for row in formula_rows),
        "non_idempotent_after_repair": len(non_idempotent),
        "ground_truth_objects_changed": list(CORRECTED_IDS),
        "ground_truth_change": OLD_PREFIX + " -> " + NEW_PREFIX,
        "json_jsonl_equal": True,
        "manifest_valid": True,
        "object_manifest_unchanged": True,
        "gt_files_reconstruct_original_sha": True,
        "gt_score_rows": len(score_rows),
        "gt_score_rows_changed": sum(
            abs(float(row["score_delta"])) > 1e-12 for row in score_rows
        ),
        "gt_score_improved": sum(float(row["score_delta"]) > 1e-12 for row in score_rows),
        "gt_score_worsened": sum(float(row["score_delta"]) < -1e-12 for row in score_rows),
        "before_sha256": before_payload["before_sha256"],
        "after_sha256": after_sha256,
        "cloud_calls": 0,
        "parser_runs": 0,
        "official_experiment_changed": False,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
