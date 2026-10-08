"""Verify and package Prompt 15 full-report artifacts."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH_MIGRATIONS = {
    "reports/prompt13/": "reports/statistical_analysis/",
    "reports\\prompt13\\": "reports\\statistical_analysis\\",
    "reports/prompt14/": "reports/error_analysis/",
    "reports\\prompt14\\": "reports\\error_analysis\\",
}
TOOLS = (
    "pymupdf", "pdfplumber", "docling", "pdfminer", "mineru",
    "ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse",
)


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


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in args.experiment_id):
        raise ValueError("Invalid experiment ID")

    output = ROOT / "reports" / "final_report" / args.experiment_id
    manifest = json.loads((output / "analysis_manifest.json").read_text(encoding="utf-8"))
    examples = read_csv(output / "examples_selected.csv")
    examples_json = json.loads((output / "examples_selected.json").read_text(encoding="utf-8"))
    references = read_csv(output / "official_documentation.csv")
    versions = read_csv(output / "tables" / "versions.csv")
    scores = read_csv(output / "tables" / "tool_scores.csv")
    prompt14 = {
        (row["tool"], row["category"], row["object_id"]): row
        for row in read_csv(ROOT / "reports" / "error_analysis" / args.experiment_id / "examples.csv")
    }
    report_md = (output / "report.md").read_text(encoding="utf-8")
    report_html = (output / "report.html").read_text(encoding="utf-8")
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: object = None) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("manifest_complete", manifest.get("status") == "complete")
    check("correct_experiment", manifest.get("experiment_id") == args.experiment_id)
    check("no_input_changes", manifest.get("inputs_changed") == [])
    check("no_adapter_or_api_calls", manifest.get("adapter_executions") == manifest.get("network_api_calls") == 0)
    check("twenty_markdown_sections", len(re.findall(r"^## \d+\. ", report_md, flags=re.MULTILINE)) == 20)
    check("twenty_html_sections", len(re.findall(r'<h2 id="[^"]+">\d+\. ', report_html)) == 20)
    check("fifty_examples", len(examples) == len(examples_json) == 50)
    check("five_examples_per_tool", Counter(row["tool"] for row in examples) == Counter({tool: 5 for tool in TOOLS}))
    check("unique_examples", len({(row["tool"], row["category"], row["object_id"]) for row in examples}) == 50)
    check(
        "examples_match_prompt14",
        all(
            (key := (row["tool"], row["category"], row["object_id"])) in prompt14
            and abs(float(row["object_score"]) - float(prompt14[key]["object_score"])) <= 1e-9
            and row["ground_truth"] == prompt14[key]["ground_truth"]
            and row["tool_output"] == prompt14[key]["tool_output"]
            for row in examples
        ),
    )
    crops = {row["source_crop"] for row in examples}
    assets = {row["prediction_asset"] for row in examples if row.get("prediction_asset")}
    check("source_crops_exist", all((output / path).is_file() and (output / path).stat().st_size > 1000 for path in crops), len(crops))
    check("prediction_assets_exist", all((output / path).is_file() and (output / path).stat().st_size > 0 for path in assets), len(assets))
    check("seventeen_figure_pairs", len(list((output / "figures").glob("*.png"))) == len(list((output / "figures").glob("*.svg"))) == 17)
    check("all_figures_used", all(f"figures/{index:02d}_" in report_md for index in range(1, 18)))
    check("ten_official_links", len(references) == 10 and all(row["url"].startswith("https://") for row in references))
    check("official_links_in_reports", all(row["url"] in report_md and row["url"] in report_html for row in references))
    check("ten_versions", len(versions) == 10 and all(row["installed_version"] in report_md for row in versions))
    check("ten_score_rows", len(scores) == 10 and all(row["name"] in report_md for row in scores))
    check("metric_formulas", all(token in report_md for token in ("S_text", "S_table", "S_math", "S_{chem,linear}", "S_{image}", "S_{diagram}", "Overall_t")))
    check("required_topics", all(token in report_md for token in ("Ground Truth", "Error analysis", "Сравнение стоимости", "Сравнение производительности", "Ограничения исследования")))
    check("input_hashes_still_match", all((path := resolve_manifest_path(relative)).is_file() and sha256(path) == digest for relative, digest in manifest["input_sha256"].items()))
    for filename in ("report.md", "report.html", "examples_selected.csv", "examples_selected.json", "official_documentation.csv", "analysis_manifest.json", "pytest_results.xml"):
        path = output / filename
        check(f"artifact:{filename}", path.is_file() and path.stat().st_size > 0)

    junit = ElementTree.parse(output / "pytest_results.xml").getroot()
    suites = [junit] if junit.tag == "testsuite" else list(junit.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    check("safe_suite_passed", tests >= 314 and failures == errors == 0, {"tests": tests, "failures": failures, "errors": errors})

    passed = all(item["passed"] for item in checks)
    verification = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": args.experiment_id,
        "status": "pass" if passed else "fail",
        "checks_passed": sum(item["passed"] for item in checks),
        "checks_failed": sum(not item["passed"] for item in checks),
        "checks": checks,
    }
    (output / "artifact_verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 1

    archive = Path(shutil.make_archive(str(output.parent / f"{args.experiment_id}_full_report"), "zip", root_dir=output))
    directory_files = {path.relative_to(output).as_posix(): sha256(path) for path in output.rglob("*") if path.is_file()}
    with zipfile.ZipFile(archive) as bundle:
        bundle_files = {info.filename: hashlib.sha256(bundle.read(info.filename)).hexdigest() for info in bundle.infolist() if not info.is_dir()}
    if bundle_files != directory_files:
        raise RuntimeError("Prompt 15 ZIP does not reproduce the artifact directory")
    print(json.dumps({"status": "pass", "checks": f"{verification['checks_passed']}/{len(checks)}", "sections": 20, "examples": len(examples), "source_crops": len(crops), "figures": 17, "tests": tests, "archive": str(archive), "archive_files": len(bundle_files)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
