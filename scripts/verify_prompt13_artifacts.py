"""Verify and package the derived Prompt 13 artifacts without touching inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FIGURES = tuple(f"{index:02d}_{name}" for index, name in enumerate((
    "overall",
    "categories_ci",
    "radar",
    "heatmap",
    "speed",
    "resources",
    "cost",
    "quality_speed",
    "quality_cost",
    "document_text",
    "document_categories",
    "category_difficulty",
    "groups",
    "object_distributions",
    "document_sensitivity",
    "paired_differences",
    "speed_by_document",
), start=1))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in args.experiment_id):
        raise ValueError("Invalid experiment ID")

    output = ROOT / "reports" / "statistical_analysis" / args.experiment_id
    manifest_path = output / "analysis_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks: list[dict[str, object]] = []
    validation = manifest.get("validation", {})
    split_reporting = (
        "chemistry_reporting_scores_reproduced" in validation
        or "chemistry_extraction_conditional_on_detection" in validation
    )

    def check(name: str, passed: bool, detail: object = None) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("analysis_manifest_complete", manifest.get("status") == "complete")
    check("correct_experiment", manifest.get("experiment_id") == args.experiment_id)
    check("input_hashes_unchanged", manifest.get("inputs_changed") == [])
    check(
        "no_acquisition_or_network",
        manifest.get("adapter_executions") == 0
        and manifest.get("network_calls_for_benchmark") == 0,
    )
    check(
        "analysis_validation",
        validation.get("canonical_category_scores_reproduced") is True
        and validation.get("operational_totals_reproduced") is True
        and (
            not split_reporting
            or (
                validation.get("chemistry_reporting_scores_reproduced") is True
                and validation.get("chemistry_extraction_conditional_on_detection") is True
            )
        )
        and validation.get("duplicate_keys") == 0
        and validation.get("nan_or_infinite_scores") == 0
        and int(validation.get("retained_quality_warnings", -1)) >= 0,
    )

    tables = sorted((output / "tables").glob("*.csv"))
    expected_table_count = 24 if split_reporting else 23
    check(
        "expected_table_count",
        len(tables) == expected_table_count,
        {"count": len(tables), "expected": expected_table_count},
    )
    check(
        "table_row_counts_recorded",
        set(manifest.get("table_row_counts", {})) == {path.stem for path in tables},
    )

    figures = manifest.get("figures", [])
    check("seventeen_manifest_figures", len(figures) == 17)
    check("expected_figure_names", tuple(item["name"] for item in figures) == EXPECTED_FIGURES)
    for name in EXPECTED_FIGURES:
        for extension in ("png", "svg"):
            path = output / "figures" / f"{name}.{extension}"
            check(f"figure:{name}.{extension}", path.is_file() and path.stat().st_size > 1_000)

    for filename in ("report.html", "report.md", "requirements-analysis-pinned.txt", "pytest_results.xml"):
        path = output / filename
        check(f"artifact:{filename}", path.is_file() and path.stat().st_size > 0)
    markdown = (output / "report.md").read_text(encoding="utf-8")
    report_is_complete = args.experiment_id in markdown
    if split_reporting:
        report_is_complete = (
            report_is_complete
            and "Chemistry Detection Score" in markdown
            and "Extraction | detected" in markdown
            and "chemistry_detection_extraction.csv" in markdown
        )
    check("report_describes_repaired_baseline", report_is_complete)

    atlas = output / "figures.pdf"
    with pymupdf.open(atlas) as document:
        atlas_pages = document.page_count
    check("pdf_atlas_seventeen_pages", atlas_pages == 17, {"pages": atlas_pages})

    junit = ElementTree.parse(output / "pytest_results.xml").getroot()
    junit_suites = [junit] if junit.tag == "testsuite" else list(junit.findall("testsuite"))
    junit_tests = sum(int(suite.attrib.get("tests", 0)) for suite in junit_suites)
    junit_failures = sum(int(suite.attrib.get("failures", 0)) for suite in junit_suites)
    junit_errors = sum(int(suite.attrib.get("errors", 0)) for suite in junit_suites)
    check(
        "safe_suite_passed",
        junit_tests >= (304 if split_reporting else 293)
        and (junit_failures, junit_errors) == (0, 0),
        {"tests": junit_tests, "failures": junit_failures, "errors": junit_errors},
    )

    # Record every finalized artifact except this self-referential file.
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
    verification_path = output / "artifact_verification.json"
    verification_path.write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not passed:
        print(json.dumps(verification, ensure_ascii=False, indent=2))
        return 1

    archive_base = output.parent / f"{args.experiment_id}_analysis"
    archive = Path(shutil.make_archive(str(archive_base), "zip", root_dir=output))
    directory_files = {
        path.relative_to(output).as_posix(): sha256(path)
        for path in output.rglob("*")
        if path.is_file()
    }
    with zipfile.ZipFile(archive) as bundle:
        bundle_files = {
            info.filename: hashlib.sha256(bundle.read(info.filename)).hexdigest()
            for info in bundle.infolist()
            if not info.is_dir()
        }
    if bundle_files != directory_files:
        raise RuntimeError("Analysis ZIP does not reproduce the finalized artifact directory")
    print(json.dumps({
        "status": "pass",
        "checks": f"{verification['checks_passed']}/{len(checks)}",
        "tables": len(tables),
        "figures": len(figures),
        "pdf_pages": atlas_pages,
        "tests": junit_tests,
        "archive": str(archive),
        "archive_files": len(bundle_files),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
