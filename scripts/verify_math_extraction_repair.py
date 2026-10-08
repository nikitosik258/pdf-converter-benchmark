"""Re-standardize cached raw results and verify shared math recovery offline.

The script never invokes adapter acquisition or a vendor API. It writes only
to ``outputs/repairs/math_extraction_v1`` and leaves the official experiment
selector unchanged.
"""

import csv
import json
from pathlib import Path

from pdf_benchmark.benchmark.config import BenchmarkConfig
from pdf_benchmark.benchmark.matching import match_document
from pdf_benchmark.benchmark.runner import BenchmarkRunner
from pdf_benchmark.benchmark.worker import run_adapter_worker
from pdf_benchmark.evaluation import EvaluationFramework, EvaluationObjectInput
from pdf_benchmark.evaluation.models import ObjectEvaluationResult
from pdf_benchmark.models import StandardizedDocument
from pdf_benchmark.normalization.pipeline import normalize_document
from pdf_benchmark.utils.io import write_json

ROOT=Path.cwd(); EXP=(ROOT/'outputs/benchmark/latest_experiment.txt').read_text(encoding='utf-8-sig').strip()
OUT=ROOT/'outputs/repairs/math_extraction_v1'
TOOLS=('pymupdf','pdfplumber','docling','pdfminer','mineru','ocr_space','nutrient','mindee','adobe_extract','llamaparse')
DOCS=('D01','D02','D03','D04','D05')
runner=BenchmarkRunner(project_root=ROOT,config=BenchmarkConfig.from_yaml(ROOT/'config/benchmark.yaml'))
framework=EvaluationFramework(runner.scoring_config)
new_results={tool:[] for tool in TOOLS}; old_results={tool:[] for tool in TOOLS}; actions=[]

for tool in TOOLS:
 for did in DOCS:
  pair_path=ROOT/'outputs/benchmark/runs'/f'exp_{EXP}_{tool}'/'pairs'/tool/did/'pair_result.json'
  pair=json.loads(pair_path.read_text(encoding='utf8'))
  target=OUT/tool/did; raw_cache=ROOT/'outputs/benchmark/cache'/tool/did
  payload=run_adapter_worker(project_root=ROOT,tool=tool,pdf_path=Path(pair['source_pdf']),document_id=did,output_dir=target,raw_cache_dir=raw_cache,seed=42,standardize_only=True)
  doc=StandardizedDocument.model_validate_json((target/'standardized.json').read_text(encoding='utf8'))
  normalized=normalize_document(doc,runner.normalization_config); write_json(target/'normalized.json',normalized.model_dump(mode='json'))
  matches=match_document(runner._gt_for_document(did),normalized,config=runner.matching_config); write_json(target/'matches.json',[m.model_dump(mode='json') for m in matches])
  evaluated=[]
  for match in matches:
   ev=framework.evaluate_object(EvaluationObjectInput(tool=tool,document_id=did,page=match.page,object_id=match.object_id,object_type=match.object_type,reference=match.reference,prediction=match.prediction,context=match.context))
   evaluated.append(ev); new_results[tool].append(ev)
  write_json(target/'object_evaluation.json',[e.model_dump(mode='json') for e in evaluated])
  old_path=Path(pair['paths']['object_evaluation'])
  old_results[tool].extend(ObjectEvaluationResult.model_validate(x) for x in json.loads(old_path.read_text(encoding='utf8')))
  actions.append({'tool':tool,'document_id':did,'action':'standardize_only','status':payload['status'],'cloud_calls':0})
  print(tool,did,payload['status'],doc.metadata.get('math_enrichment'))

def tool_scores(rows):
 records=framework.evaluate_tool(rows)
 out={r.score_name:r.score for r in records if r.scope in {'category','tool'}}
 return out,records

summary=[]; all_aggregates=[]
category_rows=[]
for tool in TOOLS:
 old,old_records=tool_scores(old_results[tool]); new,records=tool_scores(new_results[tool]); all_aggregates.extend(r.model_dump(mode='json') for r in records)
 old_math=old.get('math_score',0.0); new_math=new.get('math_score',0.0)
 summary.append({'tool':tool,'baseline_math_score':old_math,'repaired_math_score':new_math,'delta':new_math-old_math,'baseline_overall':old.get('overall_quality_score'),'repaired_overall':new.get('overall_quality_score'),'matched_math':sum(r.matched for r in new_results[tool] if r.category=='math'),'math_objects':sum(r.category=='math' for r in new_results[tool])})
 old_by_name={r.score_name:r for r in old_records if r.scope in {'category','tool'}}
 for record in records:
  if record.scope not in {'category','tool'}: continue
  baseline=old_by_name[record.score_name].score
  category_rows.append({'tool':tool,'score_name':record.score_name,'category':record.category,'baseline':baseline,'repaired':record.score,'delta':record.score-baseline})

write_json(OUT/'actions.json',actions); write_json(OUT/'summary.json',summary); write_json(OUT/'aggregate_scores.json',all_aggregates)
with (OUT/'summary.csv').open('w',encoding='utf-8-sig',newline='') as fh:
 writer=csv.DictWriter(fh,fieldnames=list(summary[0])); writer.writeheader(); writer.writerows(summary)
with (OUT/'category_comparison.csv').open('w',encoding='utf-8-sig',newline='') as fh:
 writer=csv.DictWriter(fh,fieldnames=list(category_rows[0])); writer.writeheader(); writer.writerows(category_rows)

object_rows=[]
for tool in TOOLS:
 for did in DOCS:
  repaired={x.object_id:x for x in new_results[tool] if x.document_id==did and x.category=='math'}
  baseline={x.object_id:x for x in old_results[tool] if x.document_id==did and x.category=='math'}
  matches={x['object_id']:x for x in json.loads((OUT/tool/did/'matches.json').read_text(encoding='utf8'))}
  for object_id,result in repaired.items():
   match=matches[object_id]; prediction=match.get('prediction') or {}; provenance=prediction.get('provenance') or {}
   object_rows.append({'tool':tool,'document_id':did,'object_id':object_id,'baseline_score':baseline[object_id].object_score,'repaired_score':result.object_score,'delta':result.object_score-baseline[object_id].object_score,'matched':result.matched,'source':provenance.get('source'),'prediction_element_id':match.get('prediction_element_id')})
with (OUT/'math_objects.csv').open('w',encoding='utf-8-sig',newline='') as fh:
 writer=csv.DictWriter(fh,fieldnames=list(object_rows[0])); writer.writeheader(); writer.writerows(object_rows)

non_math=[row for row in category_rows if row['category']!='math' and row['score_name']!='overall_quality_score']
verification={'official_baseline_unchanged':(ROOT/'outputs/benchmark/latest_experiment.txt').read_text().strip()==EXP,'pairs':len(actions),'successful_pairs':sum(x['status']=='success' for x in actions),'standardize_only_pairs':sum(x['action']=='standardize_only' for x in actions),'cloud_calls':sum(x['cloud_calls'] for x in actions),'non_math_score_rows':len(non_math),'non_math_changed_rows':sum(abs(x['delta'])>1e-9 for x in non_math),'max_abs_non_math_delta':max((abs(x['delta']) for x in non_math),default=0.0),'math_objects':len(object_rows),'math_matched_after':sum(x['matched'] for x in object_rows)}
write_json(OUT/'verification.json',verification)
print(json.dumps(summary,ensure_ascii=True,indent=2))
print(json.dumps(verification,ensure_ascii=True,indent=2))
