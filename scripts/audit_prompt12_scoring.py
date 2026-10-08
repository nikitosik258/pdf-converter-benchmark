"""Offline audit of Prompt 1-12 scoring. Writes diagnostics, never benchmark data.

Variants below are counterfactual probes, NOT revised official results. They
change one representation at a time, never select a variant by its GT score.
No adapter.run()/convert()/run_raw(), API or model inference. Source PDF
regions are rendered read-only for visual verification of existing GT.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pdf_benchmark.benchmark.ground_truth import load_ground_truth
from pdf_benchmark.benchmark.matching import (
    MatchingConfig, _aggregate_text_candidate, _bbox_intersection_over_smaller,
    match_document, page_candidates,
)
from pdf_benchmark.evaluation.common import levenshtein_distance
from pdf_benchmark.evaluation.framework import EvaluationFramework
from pdf_benchmark.evaluation.math_metrics import calculate_math_metrics
from pdf_benchmark.models import BBox, Caption, StandardizedDocument, Table
from pdf_benchmark.normalization.latex import normalize_latex
from pdf_benchmark.normalization.text import normalize_text
from pdf_benchmark.utils.geometry import normalize_bbox
from pdf_benchmark.utils.html_tables import parse_html_table
from pdf_benchmark.adapters.cloud.adobe_extract_adapter import AdobeExtractAdapter
from pdf_benchmark.adapters.cloud.helpers import bbox_from_normalized_polygon
from pdf_benchmark.adapters.cloud.llamaparse_adapter import _parse_markdown_table
from pdf_benchmark.adapters.local.mineru_adapter import MinerUAdapter
from pdf_benchmark.adapters.cloud.nutrient_adapter import _bounds_to_bbox

EXPERIMENT = "20261001T200058Z_s42_textdedup"
OUT = ROOT / "reports/code_audit" / EXPERIMENT
HASHES = {}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    path = Path(path)
    HASHES[str(path.relative_to(ROOT))] = sha(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(name, rows):
    if not rows:
        return
    with (OUT / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader(); writer.writerows(rows)


def edit_distance(a, b):
    """Exact unit Levenshtein with Python bit vectors, cross-checked below."""
    if not a: return len(b)
    masks = {}
    for i, token in enumerate(a): masks[token] = masks.get(token, 0) | (1 << i)
    positive, negative, score, high = ~0, 0, len(a), 1 << (len(a) - 1)
    for token in b:
        eq = masks.get(token, 0)
        d0 = (((eq & positive) + positive) ^ positive) | eq | negative
        hp = negative | ~(d0 | positive)
        hn = positive & d0
        score += int(bool(hp & high)) - int(bool(hn & high))
        hp = (hp << 1) | 1
        positive = (hn << 1) | ~(d0 | hp)
        negative = d0 & hp
    return score


def text_score(ref, pred):
    ref, pred = normalize_text(ref), normalize_text(pred)
    d = edit_distance(ref, pred)
    words = ref.split()
    dw = edit_distance(words, pred.split())
    cer = d / len(ref) if ref else float(bool(pred))
    wer = dw / len(words) if words else float(bool(pred.split()))
    sim = 1 - d / max(len(ref), len(pred)) if ref or pred else 1
    return 100 * (.5 * max(0, 1-cer) + .375 * max(0, 1-wer) + .125 * sim)


def evaluate(matches, tool):
    engine = EvaluationFramework()
    return [engine.evaluate_object({"tool": tool, "document_id": m.document_id,
        "page": m.page, "object_id": m.object_id, "object_type": m.object_type,
        "reference": m.reference, "prediction": m.prediction, "context": m.context}) for m in matches]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    baseline = read(ROOT / "outputs/benchmark/experiments" / EXPERIMENT / "source_input_hashes.json")
    for path, h in baseline.items():
        assert sha(ROOT/path) == h, path
    for base in ("src", "config", "ground_truth", "documents", "benchmark", "tests"):
        for path in (ROOT/base).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                HASHES[str(path.relative_to(ROOT))] = sha(path)
    gt = load_ground_truth(ROOT / "ground_truth")
    manifest = read(ROOT / "outputs/benchmark/experiments" / EXPERIMENT / "experiment_manifest.json")
    rng = random.Random(42)
    for _ in range(300):
        a = "".join(rng.choices("abc .-", k=rng.randrange(50)))
        b = "".join(rng.choices("abc .-", k=rng.randrange(50)))
        assert edit_distance(a, b) == levenshtein_distance(a, b)
        assert edit_distance(a.split(), b.split()) == levenshtein_distance(a.split(), b.split())

    evidence, variants, raw_shapes, pictures, missing, primitives, coverage = [], [], [], [], [], [], []
    math_evidence, visual_detection, source_pdfs = [], [], {}
    gt_map = {g.object_id:g for g in gt}

    def add_variant(tool, docid, g, variant, prediction, baseline_score, **fields):
        score = text_score(g.reference["text"], prediction)
        variants.append({"tool":tool,"document_id":docid,"object_id":g.object_id,
            "category":"text","variant":variant,"baseline_score":baseline_score,
            "diagnostic_score":score,"delta":score-baseline_score,**fields})

    def record_matches(tool, docid, variant, selected_gt, document, old_scores):
        for rec in evaluate(match_document(selected_gt, document), tool):
            old = old_scores[rec.object_id]
            variants.append({"tool":tool,"document_id":docid,"object_id":rec.object_id,
                "category":rec.category,"variant":variant,"baseline_score":old,
                "diagnostic_score":rec.object_score,"delta":rec.object_score-old})

    for tool, run in manifest["tool_runs"].items():
        for docid in ("D01","D02","D03","D04","D05"):
            folder = ROOT / "outputs/benchmark/runs" / run / "pairs" / tool / docid
            pair = read(folder / "pair_result.json")
            source_pdfs[docid] = pair["source_pdf"]
            d = StandardizedDocument.model_validate(read(Path(pair["paths"]["normalized"])))
            mm = read(folder / "matches.json"); scores = read(folder / "object_evaluation.json")
            ss = {r["object_id"]:r["object_score"] for r in scores}
            pages = {p.page_number:p for p in d.pages}
            for r in scores:
                values = r["details"]["normalized_metric_values"]
                primitives.append({"tool":tool,"document_id":docid,"object_id":r["object_id"],
                    "category":r["category"],"matched":r["matched"],"score":r["object_score"],
                    **values})
            for m in mm:
                g = gt_map[m["object_id"]]
                p = pages.get(g.page)
                if p is None: continue
                if g.object_type == "math_formula":
                    pred = m["prediction"] or {}
                    math_evidence.append({"tool":tool,"document_id":docid,"object_id":g.object_id,
                        "page":g.page,"matched":m["matched"],"match_method":m["match_method"],
                        "reference_latex":g.reference.get("latex"),
                        "compared_latex":pred.get("latex"),"baseline_score":ss[g.object_id]})
                if g.object_type in {"image","diagram"}:
                    visual_detection.append({"tool":tool,"document_id":docid,"object_id":g.object_id,
                        "page":g.page,"type":g.object_type,**m["context"]})
                if m["object_type"] == "text":
                    ref=g.reference["text"]; pred=(m["prediction"] or {}).get("text", "")
                    assert abs(text_score(ref,pred)-ss[g.object_id]) < 1e-8
                    ag,_,context = _aggregate_text_candidate(g,p,page_candidates(p,"text"),MatchingConfig())
                    ag_text = ag.payload["text"] if ag else ""
                    evidence.append({"tool":tool,"document_id":docid,"object_id":g.object_id,
                        "page":g.page,"method":m["match_method"],"reference_chars":len(ref),
                        "prediction_chars":len(pred),"baseline_score":ss[g.object_id],
                        "spatial_blocks":context.get("source_block_count",0),
                        "reference_text":ref,"compared_text":pred,"spatial_aggregate_text":ag_text})
                    add_variant(tool,docid,g,"all_spatial_blocks_existing_aggregation",ag_text,ss[g.object_id])
                    if tool == "mindee":
                        words = [w for block in p.text_blocks for w in block.provenance.get("words",[])]
                        selected = [w["content"] for w in words if (b:=bbox_from_normalized_polygon(w.get("polygon")))
                            and _bbox_intersection_over_smaller(g.bbox,b)>=.5]
                        add_variant(tool,docid,g,"mindee_native_words_in_gt_region"," ".join(selected),ss[g.object_id],native_words=len(selected))
                    if tool == "pdfminer":
                        line_candidates=[c for c in page_candidates(p,"text") if str(c.payload.get("provenance",{}).get("layout_type","")).startswith("LTTextLine")]
                        la,_,_= _aggregate_text_candidate(g,p,line_candidates,MatchingConfig())
                        add_variant(tool,docid,g,"pdfminer_leaf_lines_spatial_aggregation",la.payload["text"] if la else "",ss[g.object_id])
                if not m["matched"]:
                    other=[]
                    for typ in ("text","table","math_formula","image","diagram"):
                        if typ==g.object_type:continue
                        for c in page_candidates(p,typ):
                            if g.bbox and c.bbox and _bbox_intersection_over_smaller(g.bbox,c.bbox)>=.5:
                                other.append({"type":typ,"id":c.element_id})
                    missing.append({"tool":tool,"document_id":docid,"object_id":g.object_id,"type":g.object_type,
                        "overlapping_other_types":json.dumps(other,ensure_ascii=False),"note":"geometry_only_not_correctness"})
            coverage.append({"tool":tool,"document_id":docid,**{field:sum(len(getattr(p,field)) for p in d.pages)
                for field in ("text_blocks","tables","formulas","chemical_objects","images","diagrams")}})

            raw = Path(pair["paths"]["raw_dir"])
            doc_gt = [g for g in gt if g.document_id==docid]
            if tool == "mineru":
                native = read(raw/"saved/structured_content.json")
                td=d.model_copy(deep=True); vd=d.model_copy(deep=True)
                tp={p.page_number:p for p in td.pages};vp={p.page_number:p for p in vd.pages}
                for idx, pg in enumerate(native["pages"]):
                    pn=int(pg.get("page_idx",idx))+1
                    tab_i=im_i=dia_i=0
                    for block in pg.get("blocks",[]):
                        typ=MinerUAdapter._block_type(block)
                        if typ=="table" or "table_" in typ:
                            before=tp[pn].tables[tab_i];tab_i+=1
                            content=block.get("content")
                            if isinstance(content,str) and "<table" in content.lower():
                                rows,cols,cells=parse_html_table(content)
                                before.rows=rows;before.columns=cols;before.cells=cells;before.html=content
                            elif isinstance(content,str) and "|" in content:
                                rows,cols,cells=_parse_markdown_table(content)
                                before.rows=rows;before.columns=cols;before.cells=cells
                            raw_shapes.append({"tool":tool,"document_id":docid,"page":pn,"id":before.element_id,
                                "native_type":typ,"content_type":type(content).__name__,
                                "native_rows":before.rows,"native_cells":len(before.cells),
                                "baseline_cells":len(pages[pn].tables[tab_i-1].cells)})
                        if typ=="image" or typ.startswith("image_") or "chart" in typ:
                            image_kind=typ=="image" or typ.startswith("image_")
                            obj=vp[pn].images[im_i] if image_kind else vp[pn].diagrams[dia_i]
                            if image_kind:im_i+=1
                            else:dia_i+=1
                            src=block.get("image_source")
                            asset=raw/"saved"/src if isinstance(src,str) else None
                            caps=block.get("captions") or []
                            cap=" ".join(str(c.get("content") or "") for c in caps if isinstance(c,dict)).strip()
                            if cap:obj.caption=Caption(text=cap)
                            exists=bool(asset and asset.is_file())
                            if exists:
                                HASHES[str(asset.relative_to(ROOT))]=sha(asset)
                                obj.asset_path=str(asset)
                                if image_kind:obj.extraction_success=True
                            raw_shapes.append({"tool":tool,"document_id":docid,"page":pn,"id":obj.element_id,
                                "native_type":typ,"image_source_type":type(src).__name__,
                                "local_asset_exists":exists,"captions_count":len(caps),"caption_chars":len(cap),
                                "baseline_asset_path":getattr((pages[pn].images[im_i-1] if image_kind else pages[pn].diagrams[dia_i-1]),"asset_path")})
                record_matches(tool,docid,"mineru_native_string_tables_html_markdown",[g for g in doc_gt if g.object_type=="table"],td,ss)
                record_matches(tool,docid,"mineru_native_visual_assets_captions_only",[g for g in doc_gt if g.object_type in {"image","diagram"}],vd,ss)
            elif tool == "adobe_extract":
                paths=sorted(raw.rglob("structuredData.json"))
                native=read(paths[0]); extracted=paths[0].parent
                td=d.model_copy(deep=True);tp={p.page_number:p for p in td.pages}
                dims=AdobeExtractAdapter._page_dims(native)
                for p in td.pages:p.tables=[]
                for order, el in enumerate(native.get("elements",[])):
                    path=el.get("Path", "")
                    if not re.search(r"/Table(?:\[\d+\])?$",path):continue
                    pn=int(el.get("Page",0))+1
                    w,h=dims[pn-1]
                    file=AdobeExtractAdapter._find_csv(extracted,el.get("filePaths",[]))
                    rows,cols,cells=0,0,[]
                    if file:
                        HASHES[str(file.relative_to(ROOT))]=sha(file)
                        rows,cols,cells=AdobeExtractAdapter._csv_cells(file)
                    table=Table(element_id=f"audit_adobe_{order}",page_number=pn,
                        bbox=normalize_bbox(el["Bounds"],w,h,origin="bottom-left") if el.get("Bounds") else None,
                        rows=rows,columns=cols,cells=cells,order_index=order)
                    tp[pn].tables.append(table)
                    raw_shapes.append({"tool":tool,"document_id":docid,"page":pn,"path":path,
                        "native_cells":len(cells),"accepted_current_path_filter":path.lower().endswith("table")})
                record_matches(tool,docid,"adobe_indexed_table_paths_only",[g for g in doc_gt if g.object_type=="table"],td,ss)
            elif tool == "docling":
                native=read(raw/"document.json")
                byid={x["self_ref"]:x for x in native.get("texts",[])}
                vd=d.model_copy(deep=True)
                diagrams={obj.element_id:obj for p in vd.pages for obj in p.diagrams}
                for pic in native.get("pictures",[]):
                    meta=pic.get("meta") or {}
                    preds=(meta.get("classification") or {}).get("predictions") or []
                    best=max(preds,key=lambda x:x.get("confidence",0)) if preds else {}
                    eid=pic["self_ref"]
                    children=[byid[c["$ref"]] for c in pic.get("children",[]) if c.get("$ref") in byid]
                    text=[c.get("text","") for c in children if c.get("label") not in {"caption","footnote"} and c.get("text")]
                    pictures.append({"document_id":docid,"id":eid,"top_class":best.get("class_name"),
                        "confidence":best.get("confidence"),"class_candidates":len(preds),
                        "standardized_as_diagram":eid in diagrams,"native_child_texts":len(text),
                        "baseline_diagram_texts":len(diagrams[eid].text_elements) if eid in diagrams else None,
                        "native_text_sample":" | ".join(text)[:400]})
                    if eid in diagrams:diagrams[eid].text_elements=text
                record_matches(tool,docid,"docling_native_picture_child_text_only",[g for g in doc_gt if g.object_type=="diagram"],vd,ss)
            elif tool == "pymupdf":
                native=read(raw/"pymupdf_raw.json")
                for g in [g for g in doc_gt if g.object_type=="text"]:
                    pg=next(p for p in native["pages"] if p["page_number"]==g.page)
                    lines=[]
                    for block in pg.get("text_json",{}).get("blocks",[]):
                        for line in block.get("lines",[]):
                            spans=[s.get("text", "") for s in line.get("spans",[])
                                if (b:=normalize_bbox(s.get("bbox"),pg["width"],pg["height"]))
                                and _bbox_intersection_over_smaller(g.bbox,b)>=.5]
                            if spans:lines.append("".join(spans))
                    add_variant(tool,docid,g,"pymupdf_native_spans_in_region_preserve_lines","\n".join(lines),ss[g.object_id])
            elif tool == "nutrient":
                native=read(raw/"response.json")
                native_by_id={x["id"]:x for x in native["output"]["elements"] if x.get("id")}
                corrected=d.model_copy(deep=True)
                for pg in corrected.pages:
                    for obj in [*pg.text_blocks,*pg.tables,*pg.formulas,*pg.images,*pg.diagrams]:
                        n=native_by_id.get(obj.element_id)
                        if not n:continue
                        w=(n.get("page") or {}).get("width");h=(n.get("page") or {}).get("height")
                        if not w or not h:continue
                        box=_bounds_to_bbox(n.get("bounds") or n.get("bbox"),w,h)
                        raw_shapes.append({"tool":tool,"document_id":docid,"page":pg.page_number,"id":obj.element_id,
                            "native_width":w,"native_height":h,"pdf_width":pg.width,"pdf_height":pg.height,
                            "bbox_changed":obj.bbox!=box,
                            "baseline_bbox":str(obj.bbox),"native_scaled_bbox":str(box)})
                        obj.bbox=box
                        if isinstance(obj,Table):
                            for cell, nc in zip(obj.cells,n.get("cells") or []):
                                cell.bbox=_bounds_to_bbox(nc.get("bounds") or nc.get("bbox"),w,h)
                record_matches(tool,docid,"nutrient_native_page_dimensions_only",[g for g in doc_gt if g.object_type!="text"],corrected,ss)
                cp={p.page_number:p for p in corrected.pages}
                for m in match_document([g for g in doc_gt if g.object_type=="text"],corrected):
                    g=gt_map[m.object_id]
                    add_variant(tool,docid,g,"nutrient_native_page_dimensions_only",(m.prediction or {}).get("text",""),ss[g.object_id])
                    p=cp[g.page]
                    ca,_,_=_aggregate_text_candidate(g,p,page_candidates(p,"text"),MatchingConfig())
                    add_variant(tool,docid,g,"nutrient_dimensions_and_spatial_aggregation",ca.payload["text"] if ca else "",ss[g.object_id])
            print(f"Audited {tool}/{docid}",flush=True)

    summary=[]
    groups=collections.defaultdict(list)
    for r in variants:groups[r["tool"],r["category"],r["variant"]].append(r)
    for (tool,category,variant),rs in groups.items():
        perdoc=collections.defaultdict(list)
        for r in rs:perdoc[r["document_id"]].append(r)
        before=mean(mean(r["baseline_score"] for r in rr) for rr in perdoc.values())
        after=mean(mean(r["diagnostic_score"] for r in rr) for rr in perdoc.values())
        summary.append({"tool":tool,"category":category,"variant":variant,"baseline_macro":before,
            "diagnostic_macro":after,"delta":after-before,"n_objects":len(rs),
            "improved_objects":sum(r["delta"]>1e-8 for r in rs),"worse_objects":sum(r["delta"]< -1e-8 for r in rs),
            "scope":"diagnostic_only_not_new_benchmark"})

    checks=[]
    for g in gt:
        pred=dict(g.reference)
        if g.object_type in {"image","chemical_structure"}:pred["extraction_success"]=True
        req={"tool":"self_reference","document_id":g.document_id,"page":g.page,"object_id":g.object_id,
            "object_type":g.object_type,"reference":g.reference,"prediction":pred}
        full=EvaluationFramework().evaluate_object(req).object_score
        req["prediction"]=None
        empty=EvaluationFramework().evaluate_object(req).object_score
        checks.append({"object_id":g.object_id,"object_type":g.object_type,"perfect_score":full,"missing_score":empty})
        assert math.isclose(full,100,abs_tol=1e-9), (g.object_id,full)
        assert empty==0
    formula_probes=[]
    for a,b in [(r"\alpha x",r"\alphax"),(r"x_i",r"x_{i}"),(r"x_i y",r"x_{i} y"),
                (r"\frac{a}{b}",r"\frac{a}{b}\tag{1}"),(r"\alpha x",r"\alpha{x}")]:
        metrics=calculate_math_metrics(a,b)
        formula_probes.append({"reference":a,"prediction":b,"normalized_reference":normalize_latex(a),
            "normalized_prediction":normalize_latex(b),**metrics})

    # Rasterization is for human inspection only; it does not feed any metric.
    import pymupdf
    crop_dir = OUT / "ground_truth_checks"
    crop_dir.mkdir(exist_ok=True)
    crops = []
    for g in gt:
        if g.object_type != "math_formula" and g.object_id not in {"CHEM_001","CHEM_002"}:
            continue
        with pymupdf.open(source_pdfs[g.document_id]) as pdf:
            page = pdf[g.page-1]
            box = pymupdf.Rect(g.bbox.x_min*page.rect.width, g.bbox.y_min*page.rect.height,
                              g.bbox.x_max*page.rect.width, g.bbox.y_max*page.rect.height)
            clips = {"":box+(-8,-8,8,8), "_exact":box, "_context":box+(-40,-30,130,30)}
            for suffix, clip in clips.items():
                path = crop_dir / f"{g.object_id}{suffix}.png"
                page.get_pixmap(matrix=pymupdf.Matrix(3,3),clip=clip & page.rect).save(path)
                crops.append({"object_id":g.object_id,"document_id":g.document_id,"page":g.page,
                    "source_pdf":str(Path(source_pdfs[g.document_id]).relative_to(ROOT)),
                    "gt_bbox":g.bbox.model_dump(),"render_clip_pdf_points":list(clip & page.rect),
                    "scale":3,"image":str(path.relative_to(OUT)),"used_in_scoring":False})
    (crop_dir/"render_manifest.json").write_text(json.dumps(crops,indent=2)+"\n",encoding="utf-8")

    for name,rows in {"text_evidence":evidence,"diagnostic_object_variants":variants,
        "diagnostic_category_variants":summary,"native_to_standardized":raw_shapes,
        "docling_picture_metadata":pictures,"missing_object_context":missing,
        "object_components":primitives,"capability_counts":coverage,"metric_identity_checks":checks,
        "latex_probes":formula_probes,"math_evidence":math_evidence,
        "visual_detection_context":visual_detection}.items():write_csv(name,rows)
    HASHES[str(Path(__file__).relative_to(ROOT))]=sha(Path(__file__))
    changed=[p for p,h in {**baseline,**HASHES}.items() if sha(ROOT/p)!=h]
    assert not changed,changed
    result={"experiment_id":EXPERIMENT,"status":"audit_complete","production_changes":False,
        "new_benchmark_published":False,"network_calls":0,"adapter_executions":0,
        "protected_files_checked":len(baseline),"audit_input_hashes":HASHES,"changed_inputs":changed,
        "text_baseline_reproduced":len(evidence),"primitive_identity_checks":len(checks),
        "pdf_renderings_for_visual_review":len(crops),
        "bit_distance_comparisons":600,"counterfactuals":summary}
    (OUT/"audit_manifest.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output":str(OUT),"summaries":summary,"changed_inputs":changed},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
