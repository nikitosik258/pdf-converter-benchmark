from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .models import EvaluationReport


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    columns: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)

    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _flatten(row.get(k)) for k in columns})


def save_evaluation_report(
    report: EvaluationReport,
    output_dir: str | Path,
) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report_json = out / "evaluation_report.json"
    report_json.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )

    metrics_csv = out / "metric_records.csv"
    _write_csv(
        metrics_csv,
        [r.model_dump(mode="json") for r in report.metric_records],
    )

    objects_csv = out / "object_scores.csv"
    _write_csv(
        objects_csv,
        [
            {
                "tool": r.tool,
                "document_id": r.document_id,
                "page": r.page,
                "object_id": r.object_id,
                "object_type": r.object_type,
                "category": r.category,
                "subtype": r.subtype,
                "matched": r.matched,
                "object_score": r.object_score,
            }
            for r in report.object_results
        ],
    )

    aggregates_csv = out / "aggregate_scores.csv"
    _write_csv(
        aggregates_csv,
        [r.model_dump(mode="json") for r in report.aggregate_scores],
    )

    return {
        "evaluation_report_json": str(report_json),
        "metric_records_csv": str(metrics_csv),
        "object_scores_csv": str(objects_csv),
        "aggregate_scores_csv": str(aggregates_csv),
    }
