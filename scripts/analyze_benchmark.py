"""Prompt 13: read-only statistical analysis of a completed benchmark.

Run with .venv-analysis/Scripts/python.exe. Extraction and scoring inputs are
never rewritten. All derived data, charts and reports go under reports/statistical_analysis.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import importlib.metadata
import itertools
import json
import math
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

from analysis_statistics import (
    chemistry_reporting_scores, document_category_means, paired_bootstrap, pareto_tools, quantile,
    spearman, tool_category_means, variance_components,
)

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("text", "table", "math", "chemistry", "image", "diagram")
LABELS = {"text": "Текст", "table": "Таблицы", "math": "Математика",
          "chemistry": "Химия", "image": "Изображения", "diagram": "Диаграммы"}
NAMES = {"pymupdf": "PyMuPDF", "pdfplumber": "pdfplumber", "pdfminer": "pdfminer.six",
         "docling": "Docling", "mineru": "MinerU", "ocr_space": "OCR.Space",
         "nutrient": "Nutrient", "mindee": "Mindee", "adobe_extract": "Adobe Extract", "llamaparse": "LlamaParse"}
CLASSIC = ("pymupdf", "pdfplumber", "pdfminer")
ML = ("docling", "mineru")
LOCAL = CLASSIC + ML
CLOUD = ("ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse")
GROUPS = {"local": LOCAL, "cloud": CLOUD, "classic_local": CLASSIC, "ml_local": ML}
GROUP_LABELS = {"local": "Локальные (5)", "cloud": "Облачные (5)", "classic_local": "Классические локальные (3)", "ml_local": "ML локальные (2)"}
DOCS = ("D01", "D02", "D03", "D04", "D05")
CHEMISTRY_REPORTING_FIELDS = (
    "chemistry_detection_score",
    "chemistry_structured_extraction_score",
)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Inputs:
    def __init__(self):
        self.hashes = {}

    def track(self, path):
        self.hashes[str(path.relative_to(ROOT))] = sha(path)
        return path

    def json(self, path):
        return json.loads(self.track(path).read_text(encoding="utf-8"))

    def csv(self, path):
        with self.track(path).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def jsonl(self, path):
        return [json.loads(s) for s in self.track(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
        writer.writeheader()
        writer.writerows(rows)


def describe(values):
    return {"n": len(values), "mean": mean(values), "median": median(values),
            "sd": stdev(values) if len(values) > 1 else None,
            "q1": quantile(values, .25), "q3": quantile(values, .75), "min": min(values), "max": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id")
    args = parser.parse_args()
    experiment_id = args.experiment_id or (ROOT / "outputs/benchmark/latest_experiment.txt").read_text(encoding="utf-8").strip()
    if not experiment_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in experiment_id):
        raise ValueError("Invalid experiment ID")
    bench = ROOT / "outputs/benchmark"
    exp = bench / "experiments" / experiment_id
    out = ROOT / "reports/statistical_analysis" / experiment_id
    out.mkdir(parents=True, exist_ok=True)
    inputs = Inputs()
    requirements_path = inputs.track(ROOT / "requirements-analysis-pinned.txt")
    shutil.copy2(requirements_path, out / requirements_path.name)
    manifest = inputs.json(exp / "experiment_manifest.json")
    control_summary_path = exp / "control_prompt12_summary.json"
    control_summary = inputs.json(control_summary_path) if control_summary_path.is_file() else {}
    quality_summary = inputs.json(exp / "results/data_quality_summary.json")
    tools = inputs.csv(exp / "results/clean_results_dataset.csv")
    pairs = inputs.csv(exp / "results/pair_operational_dataset.csv")
    objects = inputs.csv(exp / "results/object_results_dataset.csv")
    warnings = inputs.csv(exp / "results/quality_issues.csv")
    preflight = inputs.json(exp / "preflight_report.json")
    assert manifest["status"] == quality_summary["status"] == "clean"
    assert (len(tools), len(pairs), len(objects)) == (10, 50, 400)
    assert {r["tool"] for r in tools} == set(LOCAL + CLOUD)
    assert len({(r["tool"], r["document_id"]) for r in pairs}) == 50
    assert len({(r["tool"], r["document_id"], r["object_id"]) for r in objects}) == 400
    score_fields = [c+"_score" for c in CATEGORIES] + ["overall_score"]
    operational_fields = ["sec_per_page", "total_processing_time_sec", "peak_ram_mb", "peak_vram_mb", "api_cost_usd"]
    for row in tools:
        for field in score_fields + operational_fields:
            row[field] = float(row[field])
            assert math.isfinite(row[field]) and row[field] >= 0
        for field in CHEMISTRY_REPORTING_FIELDS:
            raw_value = row.get(field)
            row[field] = (
                float(raw_value)
                if raw_value not in {None, ""}
                else None
            )
            if row[field] is not None:
                assert math.isfinite(row[field]) and 0 <= row[field] <= 100
        row.update(name=NAMES[row["tool"]], deployment="local" if row["tool"] in LOCAL else "cloud",
                   technology="classic_local" if row["tool"] in CLASSIC else "ml_local" if row["tool"] in ML else "cloud_unspecified")
        assert all(row[f] <= 100 for f in score_fields)
        assert math.isclose(row["overall_score"], mean(row[c+"_score"] for c in CATEGORIES), abs_tol=1e-8)
    for row in pairs:
        for key in ("processing_seconds", "sec_per_page", "peak_ram_mb", "peak_vram_mb", "api_cost_usd"):
            row[key] = float(row[key])
            assert math.isfinite(row[key]) and row[key] >= 0
        for key in ("pages", "matched_objects", "missing_objects"):
            row[key] = int(row[key])
        assert row["status"] == "success"
    for row in objects:
        row["object_score"] = float(row["object_score"])
        row["matched"] = row["matched"] == "True"
        row["page"] = int(row["page"])
        assert 0 <= row["object_score"] <= 100
        assert row["matched"] or row["object_score"] == 0

    by_tool = {r["tool"]: r for r in tools}
    assert all(r["api_cost_usd"] == 0 for r in tools), "Cost report is specific to the recorded zero-cost baseline"
    doc_scores = document_category_means(objects)
    category_scores = tool_category_means(doc_scores)
    for tool in by_tool:
        for category in CATEGORIES:
            assert math.isclose(category_scores[tool, category], by_tool[tool][category+"_score"], abs_tol=1e-8)
        op = [p for p in pairs if p["tool"] == tool]
        assert sum(p["pages"] for p in op) == 98
        assert math.isclose(sum(p["processing_seconds"] for p in op), by_tool[tool]["total_processing_time_sec"], abs_tol=1e-8)
        assert math.isclose(by_tool[tool]["sec_per_page"], by_tool[tool]["total_processing_time_sec"]/98, abs_tol=1e-8)
        for field in ("peak_ram_mb", "peak_vram_mb"):
            assert max(p[field] for p in op) == by_tool[tool][field]
        assert sum(p["api_cost_usd"] for p in op) == by_tool[tool]["api_cost_usd"]

    uncertainty, metrics, counts, provenance, versions = [], [], [], [], []
    chemistry_aggregate_records = {}
    for tool, run in manifest["tool_runs"].items():
        report = inputs.json(bench / "runs" / run / "metrics/evaluation_report.json")
        metrics.extend(report["metric_records"])
        for record in report["aggregate_scores"]:
            if record["scope"] == "category":
                canonical_name = f"{record['category']}_score"
                if record["score_name"] == canonical_name:
                    assert math.isclose(record["score"], category_scores[tool, record["category"]], abs_tol=1e-8)
                    uncertainty.append({k: record[k] for k in ("tool", "category", "score", "n_objects", "n_documents", "std_dev", "ci95_low", "ci95_high", "ci_basis")})
                elif record["score_name"] in CHEMISTRY_REPORTING_FIELDS:
                    chemistry_aggregate_records[tool, record["score_name"]] = record
            if record["scope"] == "document" and record["category"]:
                assert math.isclose(record["score"], doc_scores[tool, record["document_id"], record["category"]], abs_tol=1e-8)
        saved_pairs = inputs.jsonl(bench / "runs" / run / "pair_results.jsonl")
        for pair in saved_pairs:
            raw, usage = pair["adapter_raw_metadata"], pair["adapter_resource_usage"]
            provenance.append({"tool": tool, "document_id": pair["document_id"],
                "api_cost_basis": "not_applicable_local" if tool in LOCAL else "actual" if raw.get("actual_cost_usd") is not None else "estimated",
                "historical_pricing_note": raw.get("pricing_basis", raw.get("quota_basis", "not_applicable_local")),
                "credits_estimate": raw.get("credits_estimate"), "provider_credits": raw.get("provider_credits"),
                "raw_processing_seconds": raw.get("processing_seconds"), "raw_latency_seconds": raw.get("latency_seconds"),
                "adapter_wall_time_seconds": usage.get("wall_time_seconds"),
                "ram_scope": "local_process_tree" if tool in LOCAL else "cloud_client_only",
                "gpu_process_mb": usage.get("gpu_peak_process_mb"), "gpu_device_mb": usage.get("gpu_peak_device_used_mb"),
                "torch_allocated_mb_diagnostic": usage.get("torch_peak_allocated_mb"),
                "torch_reserved_mb_diagnostic": usage.get("torch_peak_reserved_mb")})
            norm = inputs.json(Path(pair["paths"]["normalized"]))
            count = {c: sum(len(p.get(field, [])) for p in norm["pages"]) for c, field in
                     zip(CATEGORIES, ("text_blocks", "tables", "formulas", "chemical_objects", "images", "diagrams"))}
            counts.append({"tool": tool, "document_id": pair["document_id"], **count})
        version = saved_pairs[0]["adapter_version_snapshot"]
        versions.append({"tool": tool, "deployment": by_tool[tool]["deployment"], "technology": by_tool[tool]["technology"],
                         "installed_version": version["installed_version"], "model_versions": json.dumps(version.get("model_versions", {}), ensure_ascii=False)})
    assert len(uncertainty) == 60

    chemistry_scores = chemistry_reporting_scores(objects, metrics)
    chemistry_reporting = []
    for tool in by_tool:
        independently_reproduced = chemistry_scores[tool]
        for field in CHEMISTRY_REPORTING_FIELDS:
            stored = by_tool[tool][field]
            reproduced = independently_reproduced[field]
            if stored is None:
                assert (tool, field) not in chemistry_aggregate_records
                continue
            assert reproduced is not None
            assert math.isclose(stored, reproduced, abs_tol=1e-8)
            assert math.isclose(
                chemistry_aggregate_records[tool, field]["score"],
                reproduced,
                abs_tol=1e-8,
            )
        chemistry_objects = [
            row for row in objects
            if row["tool"] == tool and row["category"] == "chemistry"
        ]
        linear = [row for row in chemistry_objects if row["subtype"] == "linear_formula"]
        structures = [row for row in chemistry_objects if row["subtype"] == "structure"]
        chemistry_reporting.append({
            "tool": tool,
            "chemistry_score": by_tool[tool]["chemistry_score"],
            "chemistry_detection_score": independently_reproduced["chemistry_detection_score"],
            "chemistry_structured_extraction_score": independently_reproduced["chemistry_structured_extraction_score"],
            "detected_objects": sum(row["matched"] for row in chemistry_objects),
            "gt_objects": len(chemistry_objects),
            "detected_linear_formulas": sum(row["matched"] for row in linear),
            "gt_linear_formulas": len(linear),
            "detected_structures": sum(row["matched"] for row in structures),
            "gt_structures": len(structures),
            "structured_extraction_denominator": "detected_objects_only",
            "included_in_overall": False,
        })

    # Independent, descriptive distributions of GT objects; not replacement scores.
    variance, dependence = [], []
    for tool in by_tool:
        for category in CATEGORIES:
            rows = [r for r in objects if r["tool"] == tool and r["category"] == category]
            values = [r["object_score"] for r in rows]
            matched = [r["object_score"] for r in rows if r["matched"]]
            variance.append({"tool": tool, "category": category, **describe(values), **variance_components(rows),
                "matched": len(matched), "missing": len(rows)-len(matched), "matched_fraction": len(matched)/len(rows),
                "zero_score_objects": sum(v == 0 for v in values), "matched_only_mean": mean(matched) if matched else None,
                "canonical_category_score": category_scores[tool, category]})
            docs = [(d, doc_scores[tool, d, category]) for d in DOCS if (tool, d, category) in doc_scores]
            dependence.append({"tool": tool, "category": category, "n_documents": len(docs),
                "document_sd": stdev(v for _, v in docs) if len(docs) > 1 else None,
                "range": max(v for _, v in docs)-min(v for _, v in docs),
                "min_document": min(docs, key=lambda p:p[1])[0], "min_score": min(v for _, v in docs),
                "max_document": max(docs, key=lambda p:p[1])[0], "max_score": max(v for _, v in docs)})

    coverage = collections.Counter((r["document_id"], r["category"]) for r in objects if r["tool"] == tools[0]["tool"])
    coverage_rows = [{"document_id": d, **{c: coverage[d,c] for c in CATEGORIES}, "total": sum(coverage[d,c] for c in CATEGORIES)} for d in DOCS]
    doc_rows = [{"tool": t, "document_id": d, "category": c, "score": value, "n_objects": coverage[d,c]} for (t,d,c),value in sorted(doc_scores.items())]
    doc_summary = []
    for tool in by_tool:
        for doc in DOCS:
            present = {c:doc_scores[tool,doc,c] for c in CATEGORIES if (tool,doc,c) in doc_scores}
            doc_summary.append({"tool":tool,"document_id":doc,"n_categories":len(present),
                "present_categories": ";".join(present), "overall_present_diagnostic":mean(present.values()),
                "common_text_table_diagram_diagnostic":mean(present[c] for c in ("text","table","diagram")),
                **{c:present.get(c) for c in CATEGORIES}})
    document_difficulty = []
    for doc in DOCS:
        document_difficulty.append({"document_id": doc, "common_three_mean_across_fixed_tools": mean(r["common_text_table_diagram_diagnostic"] for r in doc_summary if r["document_id"] == doc),
            **{c: mean(doc_scores[t,doc,c] for t in by_tool) if coverage[doc,c] else None for c in CATEGORIES}})
    lodo = []
    for tool in by_tool:
        for omitted in DOCS:
            subset = {(t,d,c):v for (t,d,c),v in doc_scores.items() if t==tool and d!=omitted}
            cats = tool_category_means(subset)
            missing = [c for c in CATEGORIES if (tool,c) not in cats]
            overall = None if missing else mean(cats[tool,c] for c in CATEGORIES)
            lodo.append({"tool":tool,"omitted_document":omitted,"missing_categories":";".join(missing),
                "overall_score":overall,"overall_delta":None if overall is None else overall-by_tool[tool]["overall_score"],
                **{c+"_score":cats.get((tool,c)) for c in CATEGORIES}})

    group_rows = []
    for group, members in GROUPS.items():
        group_rows.append({"group":group,"n_tools":len(members), **{f:mean(by_tool[t][f] for t in members) for f in score_fields},
                           **{f+"_median":median(by_tool[t][f] for t in members) for f in operational_fields}})
    comparisons = []
    for left,right in itertools.combinations(by_tool, 2):
        for category in CATEGORIES:
            docs = [d for d in DOCS if coverage[d,category]]
            a,b = [doc_scores[left,d,category] for d in docs], [doc_scores[right,d,category] for d in docs]
            delta,lo,hi = paired_bootstrap(a,b)
            comparisons.append({"left":left,"right":right,"category":category,"n_documents":len(docs),
                                "delta":delta,"ci95_low":lo,"ci95_high":hi,
                                "ci_basis":"paired_document_bootstrap" if len(docs)>1 else "insufficient_documents",
                                "multiplicity_adjusted":False})
    group_comparisons = []
    for left,right in (("local","cloud"),("ml_local","classic_local")):
        for category in CATEGORIES:
            docs = [d for d in DOCS if coverage[d,category]]
            a = [mean(doc_scores[t,d,category] for t in GROUPS[left]) for d in docs]
            b = [mean(doc_scores[t,d,category] for t in GROUPS[right]) for d in docs]
            delta,lo,hi = paired_bootstrap(a,b)
            group_comparisons.append({"left":left,"right":right,"category":category,"n_documents":len(docs),
                "delta":delta,"ci95_low":lo,"ci95_high":hi,"scope":"fixed_tools_resampled_documents"})

    difficulty = []
    for category in CATEGORIES:
        values = [by_tool[t][category+"_score"] for t in by_tool]
        cat_objects = [r for r in objects if r["category"]==category]
        top = max(values)
        difficulty.append({"category":category,"mean_across_fixed_tools":mean(values),"median_across_fixed_tools":median(values),
            "tools_with_nonzero_score":sum(v>0 for v in values),"matched_object_evaluations":sum(r["matched"] for r in cat_objects),
            "object_evaluations":len(cat_objects),"best_score":top,
            "top_tools_at_0_01_precision":";".join(t for t in by_tool if top-by_tool[t][category+"_score"]<.005),
            "n_gt_objects":len(cat_objects)//len(tools),"n_documents":sum(bool(coverage[d,category]) for d in DOCS)})
    speed_rows = []
    for tool in by_tool:
        pp = [p for p in pairs if p["tool"]==tool]
        slow = max(pp,key=lambda p:p["sec_per_page"])
        remaining = [p for p in pp if p["document_id"]!=slow["document_id"]]
        speed_rows.append({"tool":tool,"corpus_sec_per_page":by_tool[tool]["sec_per_page"],
            "median_document_sec_per_page":median(p["sec_per_page"] for p in pp),
            "min_document_sec_per_page":min(p["sec_per_page"] for p in pp),"slowest_document":slow["document_id"],
            "max_document_sec_per_page":slow["sec_per_page"],"slowest_to_median_ratio":slow["sec_per_page"]/median(p["sec_per_page"] for p in pp),
            "slowest_document_time_share":slow["processing_seconds"]/by_tool[tool]["total_processing_time_sec"],
            "sec_per_page_without_slowest_diagnostic":sum(p["processing_seconds"] for p in remaining)/sum(p["pages"] for p in remaining)})
    frontier = sorted(pareto_tools(tools), key=lambda t:by_tool[t]["sec_per_page"])
    category_frontiers = {c:sorted(pareto_tools([r for r in tools if r[c+"_score"]>0], quality=c+"_score"), key=lambda t:by_tool[t]["sec_per_page"]) for c in CATEGORIES}
    metric_groups = collections.defaultdict(list)
    for metric in metrics:
        metric_groups[metric["tool"],metric["category"],metric["metric_name"]].append(metric)
    metric_summary = [{"tool":t,"category":c,"metric_name":m,"n_objects":len(rows),
        "raw_mean":mean(r["raw_value"] for r in rows if r["raw_value"] is not None),
        "quality_mean_0_100":mean(r["metric_value"] for r in rows),
        "aggregation":"object_micro_descriptive_not_category_score"} for (t,c,m),rows in sorted(metric_groups.items())]
    correlations = [{"quality":q,"operational_metric":op,"n_tools":10,
        "spearman_rho":spearman([r[q] for r in tools],[r[op] for r in tools]),"scope":"descriptive_fixed_roster"}
        for q in score_fields for op in ("sec_per_page","peak_ram_mb","peak_vram_mb","api_cost_usd")]

    tables = {"tool_scores":tools,"pair_operational":pairs,"object_scores":objects,"category_uncertainty":uncertainty,
        "document_category_scores":doc_rows,"document_diagnostics":doc_summary,"ground_truth_coverage":coverage_rows,
        "category_difficulty":difficulty,"document_difficulty":document_difficulty,"object_variance":variance,
        "document_dependence":dependence,"leave_one_document_out":lodo,"group_scores":group_rows,
        "paired_category_differences":comparisons,"paired_group_differences":group_comparisons,
        "speed_sensitivity":speed_rows,"operational_provenance":provenance,"standardized_object_counts":counts,
        "metric_diagnostics":metric_summary,"chemistry_detection_extraction":chemistry_reporting,
        "versions":versions,"quality_issues":warnings,"correlations":correlations,
        "pareto_frontiers":[{"scope":"overall","tool":t} for t in frontier]+[{"scope":c,"tool":t} for c,ts in category_frontiers.items() for t in ts]}
    for name,rows in tables.items():
        write_csv(out / "tables" / (name+".csv"),rows)

    context = {"experiment_id":experiment_id,
        "source_experiment_id":control_summary.get("source_experiment_id") or manifest.get("source_experiment_id"),
        "tools":tools,"by_tool":by_tool,"tables":tables,"doc_scores":doc_scores,"coverage":coverage,
        "frontier":frontier,"category_frontiers":category_frontiers,"out":out,"input_hashes":inputs.hashes,
        "category_labels":LABELS,"tool_names":NAMES,"categories":CATEGORIES,"documents":DOCS,
        "group_labels":GROUP_LABELS,"groups":GROUPS,"pdfs":preflight["pdfs"],
        "quality_summary":quality_summary,"experiment_manifest":manifest}
    print(f"Validated 10 tools / 50 pairs / 400 objects; wrote {len(tables)} tables",flush=True)
    from plot_benchmark_analysis import make_figures
    from render_benchmark_analysis import write_reports
    figures = make_figures(context)
    write_reports(context,figures)
    for path in (Path(__file__), ROOT/"scripts/analysis_statistics.py", ROOT/"scripts/plot_benchmark_analysis.py", ROOT/"scripts/render_benchmark_analysis.py"):
        inputs.track(path)
    changed = [p for p,h in inputs.hashes.items() if sha(ROOT/p)!=h]
    assert not changed,changed
    requirements = {p:importlib.metadata.version(p) for p in ("matplotlib","numpy")}
    analysis_manifest = {"experiment_id":experiment_id,"created_at":datetime.now(timezone.utc).isoformat(),
        "status":"complete","seed":42,"bootstrap_resamples":10000,"python_version":platform.python_version(),
        "packages":requirements,"input_hashes":inputs.hashes,"inputs_changed":changed,
        "table_row_counts":{n:len(rows) for n,rows in tables.items()},"figures":figures,
        "validation":{"canonical_category_scores_reproduced":True,"operational_totals_reproduced":True,
                      "chemistry_reporting_scores_reproduced":True,
                      "chemistry_extraction_conditional_on_detection":True,
                      "duplicate_keys":0,"nan_or_infinite_scores":0,"retained_quality_warnings":len(warnings)},
        "overall_ci":"not_estimated_incomplete_category_coverage",
        "pairwise_ci":"exploratory_paired_document_bootstrap_not_multiplicity_adjusted",
        "cost_quality":"recorded_costs_zero_ratio_and_correlation_undefined",
        "network_calls_for_benchmark":0,"adapter_executions":0}
    (out/"analysis_manifest.json").write_text(json.dumps(analysis_manifest,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    archive = shutil.make_archive(str(out.parent/(experiment_id+"_analysis")),"zip",root_dir=out)
    print(json.dumps({"report":str(out/"report.html"),"archive":archive,"tables":len(tables),"figures":len(figures),"inputs_changed":changed},indent=2))


if __name__ == "__main__":
    main()
