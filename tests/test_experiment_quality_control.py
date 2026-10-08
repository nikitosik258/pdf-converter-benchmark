from __future__ import annotations

import json
from pathlib import Path

from pdf_benchmark.benchmark.config import BenchmarkConfig, DocumentConfig
from pdf_benchmark.benchmark.experiment import (
    build_clean_results_dataset,
    validate_ground_truth_content,
    validate_tool_run,
)
from pdf_benchmark.benchmark.ground_truth import GroundTruthObject
from pdf_benchmark.models import BBox


def _cfg(tools: list[str]) -> BenchmarkConfig:
    docs = [
        DocumentConfig(document_id="D01", filename="d1.pdf"),
        DocumentConfig(document_id="D02", filename="d2.pdf"),
        DocumentConfig(document_id="D03", filename="d3.pdf"),
        DocumentConfig(document_id="D04", filename="d4.pdf"),
        DocumentConfig(document_id="D05", filename="d5.pdf"),
    ]
    return BenchmarkConfig(
        seed=42,
        output_root="outputs/benchmark",
        ground_truth_dir="ground_truth",
        object_manifest="benchmark/object_manifest.json",
        normalization_config="config/normalization.yaml",
        metrics_config="config/metrics.yaml",
        matching_config="config/matching.yaml",
        documents=docs,
        tools=tools,
    )


def test_gt_content_detects_placeholder_and_missing_bbox():
    obj = GroundTruthObject(
        object_id="TXT_1",
        document_id="D01",
        page=1,
        object_type="text",
        bbox=None,
        reference={"text": "..."},
    )
    issues = validate_ground_truth_content([obj], require_bbox_all=True)
    codes = {x.code for x in issues}
    assert "gt_placeholder" in codes
    assert "gt_missing_bbox" in codes


def test_gt_valid_text_is_clean():
    obj = GroundTruthObject(
        object_id="TXT_1",
        document_id="D01",
        page=1,
        object_type="text",
        bbox=BBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.3),
        reference={"text": "Нормальный эталонный текст"},
    )
    issues = validate_ground_truth_content([obj], require_bbox_all=True)
    assert not [x for x in issues if x.severity == "error"]


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_synthetic_tool_run(root: Path, cfg: BenchmarkConfig, tool: str, run_id: str, score: float = 80.0):
    run_dir = root / cfg.output_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest_objects = []
    pair_rows = []
    object_index = 0
    for doc in cfg.documents:
        object_index += 1
        oid = f"OBJ_{doc.document_id}"
        otype = ["text", "table", "math_formula", "chemical_formula", "image"][object_index - 1]
        manifest_objects.append({
            "object_id": oid,
            "document_id": doc.document_id,
            "page": 1,
            "object_type": otype,
        })
        cache = root / cfg.output_root / "cache" / tool / doc.document_id
        raw = cache / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "raw.json").write_text("{}", encoding="utf-8")
        standardized = cache / "standardized.json"
        normalized = cache / "normalized.json"
        matches = cache / "matches.json"
        obj_eval = cache / "object_evaluation.json"
        doc_payload = {
            "schema_version": "1.0",
            "document_id": doc.document_id,
            "source_pdf": doc.filename,
            "tool": {
                "tool_name": tool,
                "distribution_name": tool,
                "version": "1.0",
                "configuration": {},
                "model_versions": {},
            },
            "pages": [{
                "page_number": 1,
                "width": 100,
                "height": 100,
                "text_blocks": [], "tables": [], "formulas": [], "chemical_objects": [], "images": [], "diagrams": [], "reading_order": [],
            }],
            "raw_artifacts": [str(raw / "raw.json")],
            "metadata": {},
        }
        _write_json(standardized, doc_payload)
        _write_json(normalized, doc_payload)
        _write_json(matches, [{"object_id": oid, "matched": True}])
        eval_payload = [{
            "tool": tool,
            "document_id": doc.document_id,
            "page": 1,
            "object_id": oid,
            "object_type": otype,
            "category": {
                "text": "text", "table": "table", "math_formula": "math", "chemical_formula": "chemistry", "image": "image"
            }[otype],
            "subtype": "linear_formula" if otype == "chemical_formula" else None,
            "matched": True,
            "metrics": [],
            "object_score": score,
            "details": {},
        }]
        _write_json(obj_eval, eval_payload)
        snap_dir = run_dir / "pairs" / tool / doc.document_id
        snap_dir.mkdir(parents=True, exist_ok=True)
        _write_json(snap_dir / "object_evaluation.json", eval_payload)
        _write_json(snap_dir / "matches.json", [{"object_id": oid, "matched": True}])
        pair_rows.append({
            "tool": tool,
            "tool_kind": "local" if tool in {"pymupdf", "pdfplumber", "docling", "marker", "mineru"} else "cloud",
            "document_id": doc.document_id,
            "status": "success",
            "object_count": 1,
            "matched_count": 1,
            "missing_count": 0,
            "paths": {
                "raw_dir": str(raw),
                "standardized": str(standardized),
                "normalized": str(normalized),
                "matches": str(matches),
                "object_evaluation": str(obj_eval),
            },
            "adapter_resource_usage": {
                "wall_time_seconds": 10.0,
                "peak_process_tree_rss_mb": 512.0,
                "gpu_peak_process_mb": None,
                "torch_peak_reserved_mb": None,
                "torch_peak_allocated_mb": None,
            },
            "adapter_raw_metadata": {
                "processing_seconds": 8.0,
                "estimated_cost_usd": 0.0,
                "actual_cost_usd": None,
            },
            "adapter_version_snapshot": {
                "installed_version": "1.0",
                "pinned_version": "1.0",
            },
            "adapter_action": "run_adapter",
        })

    # Add diagram object to D05 so all six categories are represented.
    extra = {"object_id": "OBJ_DIA", "document_id": "D05", "page": 1, "object_type": "diagram"}
    manifest_objects.append(extra)
    pair_rows[-1]["object_count"] = 2
    pair_rows[-1]["matched_count"] = 2
    obj_eval_path = Path(pair_rows[-1]["paths"]["object_evaluation"])
    objs = json.loads(obj_eval_path.read_text(encoding="utf-8"))
    dia = {
        "tool": tool, "document_id": "D05", "page": 1, "object_id": "OBJ_DIA",
        "object_type": "diagram", "category": "diagram", "subtype": None,
        "matched": True, "metrics": [], "object_score": score, "details": {},
    }
    objs.append(dia)
    _write_json(obj_eval_path, objs)
    _write_json(run_dir / "pairs" / tool / "D05" / "object_evaluation.json", objs)

    _write_json(root / cfg.object_manifest, {"objects": manifest_objects})

    with (run_dir / "pair_results.jsonl").open("w", encoding="utf-8") as fh:
        for pair in pair_rows:
            fh.write(json.dumps(pair) + "\n")

    aggregates = []
    for category in ["text", "table", "math", "chemistry", "image", "diagram"]:
        aggregates.append({
            "tool": tool, "scope": "category", "score_name": f"{category}_score", "score": score,
            "document_id": None, "page": None, "category": category,
        })
    aggregates.extend([
        {
            "tool": tool, "scope": "category",
            "score_name": "chemistry_detection_score", "score": 100.0,
            "document_id": None, "page": None, "category": "chemistry",
        },
        {
            "tool": tool, "scope": "category",
            "score_name": "chemistry_structured_extraction_score", "score": 80.0,
            "document_id": None, "page": None, "category": "chemistry",
        },
    ])
    aggregates.append({
        "tool": tool, "scope": "tool", "score_name": "overall_quality_score", "score": score,
        "document_id": None, "page": None, "category": None,
    })
    _write_json(run_dir / "metrics" / "evaluation_report.json", {"aggregate_scores": aggregates})
    _write_json(run_dir / "run_manifest.json", {
        "run_id": run_id,
        "status": "success",
        "selected_tools": [tool],
        "selected_documents": [d.document_id for d in cfg.documents],
    })
    return run_dir, manifest_objects


def test_validate_tool_run_clean_synthetic(tmp_path):
    cfg = _cfg(["pymupdf"])
    (tmp_path / "benchmark").mkdir(parents=True)
    run_dir, _ = _make_synthetic_tool_run(tmp_path, cfg, "pymupdf", "r1", 80.0)
    report = validate_tool_run(tmp_path, run_dir, "pymupdf", cfg)
    assert report["status"] == "clean", report
    assert report["error_count"] == 0


def test_build_clean_results_dataset_has_requested_columns(tmp_path):
    cfg = _cfg(["pymupdf"])
    (tmp_path / "benchmark").mkdir(parents=True)
    _make_synthetic_tool_run(tmp_path, cfg, "pymupdf", "r1", 80.0)
    experiment_dir = tmp_path / cfg.output_root / "experiments" / "e1"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    integrity = {"pymupdf": {"status": "clean", "error_count": 0, "warning_count": 0}}
    summary = build_clean_results_dataset(
        tmp_path,
        experiment_dir,
        {"pymupdf": "r1"},
        integrity,
        cfg,
        {
            "expected_pages": {"D01": 1, "D02": 1, "D03": 1, "D04": 1, "D05": 1},
            "expected_total_pages": 5,
            "outlier_iqr_multiplier": 1.5,
        },
    )
    assert summary["status"] == "clean", summary
    csv_path = experiment_dir / "results" / "clean_results_dataset.csv"
    header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    required = {
        "tool", "text_score", "table_score", "math_score", "chemistry_score",
        "chemistry_detection_score", "chemistry_structured_extraction_score",
        "image_score", "diagram_score", "overall_score", "sec_per_page",
        "total_processing_time_sec", "peak_ram_mb", "peak_vram_mb",
        "api_cost_usd", "errors_count",
    }
    assert required <= set(header)


def test_failed_integrity_excludes_tool_from_clean_dataset(tmp_path):
    cfg = _cfg(["pymupdf"])
    (tmp_path / "benchmark").mkdir(parents=True)
    _make_synthetic_tool_run(tmp_path, cfg, "pymupdf", "r1", 80.0)
    experiment_dir = tmp_path / cfg.output_root / "experiments" / "e1"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    summary = build_clean_results_dataset(
        tmp_path,
        experiment_dir,
        {"pymupdf": "r1"},
        {"pymupdf": {"status": "corrupt", "error_count": 1, "warning_count": 0}},
        cfg,
        {
            "expected_pages": {"D01": 1, "D02": 1, "D03": 1, "D04": 1, "D05": 1},
            "expected_total_pages": 5,
            "outlier_iqr_multiplier": 1.5,
        },
    )
    assert summary["status"] == "needs_attention"
    assert summary["clean_tool_rows"] == 0
