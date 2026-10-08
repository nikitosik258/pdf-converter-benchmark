"""Verify and package Prompt 14 error-analysis artifacts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
TOOLS = (
    "pymupdf", "pdfplumber", "docling", "pdfminer", "mineru",
    "ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse",
)
CATEGORIES = ("text", "table", "math", "chemistry", "image", "diagram")
REPORT_PATH_MIGRATIONS = {
    "reports/prompt13/": "reports/statistical_analysis/",
    "reports\\prompt13\\": "reports\\statistical_analysis\\",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def resolve_manifest_path(relative: str) -> Path:
    """Resolve historic provenance paths after the reports-directory rename."""
    for legacy, current in REPORT_PATH_MIGRATIONS.items():
        if relative.startswith(legacy):
            relative = current + relative[len(legacy):]
            break
    return ROOT / relative


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in args.experiment_id):
        raise ValueError("Invalid experiment ID")
    output = ROOT / "reports/error_analysis" / args.experiment_id
    manifest = json.loads((output / "analysis_manifest.json").read_text(encoding="utf-8"))
    rows = read_csv(output / "examples.csv")
    full = json.loads((output / "examples.json").read_text(encoding="utf-8"))
    summary = read_csv(output / "tool_summary.csv")
    official = {
        (row["tool"], row["document_id"], row["object_id"]): row
        for row in read_csv(ROOT / "outputs/benchmark/experiments" / args.experiment_id / "results/object_results_dataset.csv")
    }
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: object = None) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("manifest_complete", manifest.get("status") == "complete")
    check("correct_experiment", manifest.get("experiment_id") == args.experiment_id)
    check("no_input_changes", manifest.get("inputs_changed") == [])
    check("no_adapter_or_network", manifest.get("adapter_executions") == manifest.get("network_calls") == 0)
    check("sixty_examples", len(rows) == len(full) == 60)
    check("ten_tools", Counter(row["tool"] for row in rows) == Counter({tool: 6 for tool in TOOLS}))
    check("six_categories", Counter(row["category"] for row in rows) == Counter({category: 10 for category in CATEGORIES}))
    check("unique_tool_category", len({(row["tool"], row["category"]) for row in rows}) == 60)
    check("tool_summary", len(summary) == 10 and all(int(row["examples"]) == 6 for row in summary))
    check(
        "required_analysis_fields",
        all(
            row.get("source_crop") and row.get("ground_truth") and row.get("tool_output")
            and row.get("error_types") and row.get("cause") and row.get("metric_impact")
            for row in rows
        ),
    )
    check(
        "scores_match_official",
        all(
            (key := (row["tool"], row["document_id"], row["object_id"])) in official
            and abs(float(row["object_score"]) - float(official[key]["object_score"])) <= 1e-8
            and row["matched"] == official[key]["matched"]
            for row in rows
        ),
    )
    crops = {row["source_crop"] for row in rows}
    check(
        "source_crops_exist",
        len(crops) == manifest.get("source_crops")
        and all((output / path).is_file() and (output / path).stat().st_size > 1000 for path in crops),
        {"unique_crops": len(crops)},
    )
    assets = {row["prediction_asset"] for row in rows if row.get("prediction_asset")}
    check(
        "prediction_assets_exist",
        len(assets) == manifest.get("prediction_assets")
        and all((output / path).is_file() and (output / path).stat().st_size > 0 for path in assets),
        {"assets": len(assets)},
    )
    check(
        "full_payloads_and_metrics",
        all(
            item.get("reference_payload") is not None
            and "prediction_payload" in item
            and isinstance(item.get("nearby_output"), list)
            and isinstance(item.get("metrics"), list) and item["metrics"]
            for item in full
        ),
    )
    check(
        "input_hashes_still_match",
        all((path := resolve_manifest_path(relative)).is_file() and sha256(path) == digest for relative, digest in manifest["input_sha256"].items()),
    )
    for filename in ("report.md", "report.html", "examples.csv", "examples.json", "tool_summary.csv", "pytest_results.xml"):
        path = output / filename
        check(f"artifact:{filename}", path.is_file() and path.stat().st_size > 0)
    report = (output / "report.md").read_text(encoding="utf-8")
    check("report_lists_all_tools", all(name in report for name in ("PyMuPDF", "pdfplumber", "Docling", "pdfminer.six", "MinerU", "OCR.Space", "Nutrient", "Mindee", "Adobe Extract", "LlamaParse")))
    check("report_has_sixty_cases", report.count("### ") == 60)

    junit = ElementTree.parse(output / "pytest_results.xml").getroot()
    suites = [junit] if junit.tag == "testsuite" else list(junit.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    check("safe_suite_passed", tests >= 314 and failures == errors == 0, {"tests": tests, "failures": failures, "errors": errors})

    # Verify that every crop is a readable raster via PyMuPDF's image decoder.
    readable = True
    for relative in crops:
        try:
            pixmap = pymupdf.Pixmap(output / relative)
            readable = readable and pixmap.width > 0 and pixmap.height > 0
        except Exception:
            readable = False
    check("source_crops_readable", readable)

    artifact_hashes = {
        path.relative_to(output).as_posix(): sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "artifact_verification.json"
    }
    passed = all(item["passed"] for item in checks)
    verification = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": args.experiment_id,
        "status": "pass" if passed else "fail",
        "checks_passed": sum(item["passed"] for item in checks),
        "checks_failed": sum(not item["passed"] for item in checks),
        "checks": checks,
        "artifact_sha256": artifact_hashes,
    }
    (output / "artifact_verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 1

    archive = Path(shutil.make_archive(str(output.parent / f"{args.experiment_id}_error_analysis"), "zip", root_dir=output))
    directory_files = {
        path.relative_to(output).as_posix(): sha256(path)
        for path in output.rglob("*") if path.is_file()
    }
    with zipfile.ZipFile(archive) as bundle:
        bundle_files = {
            info.filename: hashlib.sha256(bundle.read(info.filename)).hexdigest()
            for info in bundle.infolist() if not info.is_dir()
        }
    if bundle_files != directory_files:
        raise RuntimeError("Prompt 14 ZIP does not reproduce the artifact directory")
    print(json.dumps({
        "status": "pass", "checks": f"{verification['checks_passed']}/{len(checks)}",
        "examples": len(rows), "tools": len(TOOLS), "categories": len(CATEGORIES),
        "source_crops": len(crops), "prediction_assets": len(assets), "tests": tests,
        "archive": str(archive), "archive_files": len(bundle_files),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
