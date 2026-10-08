# CODEX_CONTEXT.md

## 1. Project identity

Project: PDF -> TXT / structured document extraction benchmark.

Repository root on the user's machine:

```text
C:\Users\Nocomp\Desktop\nlp
```

Primary OS / environment:

```text
Windows 11
Python 3.12.10
PowerShell
GPU: NVIDIA RTX 4070 Laptop, 8 GB
NVIDIA driver observed: 592.00
nvidia-smi CUDA: 13.1
```

The project compares **10 document/PDF extraction tools** on a fixed corpus of **5 technical PDFs** with manually prepared Ground Truth.

This repository is already at a late benchmark stage. Do **not** redesign it from scratch. Preserve reproducibility, the fixed corpus, Ground Truth, cached vendor responses, and existing benchmark semantics unless explicitly asked to change them.

---

## 2. Current benchmark objective

The benchmark evaluates PDF extraction quality across six categories:

1. Text
2. Tables
3. Math formulas
4. Chemistry
5. Images
6. Diagrams / schemes

The final tool dataset also tracks:

- Overall Score
- sec/page
- total processing time
- RAM
- VRAM
- API cost
- error count

The benchmark is intended for Russian/technical PDFs and includes structured evaluation of representative GT objects rather than exhaustive annotation of every object in every PDF.

---

## 3. Fixed PDF corpus

These five PDFs are the canonical benchmark corpus and must not be replaced or edited without explicit approval.

### D01

```text
document_id: D01
filename: 3619.pdf
pages: 43
size_bytes: 513518
sha256: 8e32c53b1c687147aa7941f8d1884db08f8e01e608e8164ee1cad6c078fe91fc
```

### D02

```text
document_id: D02
filename: 369.pdf
pages: 7
size_bytes: 719339
sha256: a2daa906b557aefd5941c2f4c0a397e9d61d31a5a9e233a2ccea7bfdb7c9c081
```

### D03

```text
document_id: D03
filename: mais702.pdf
pages: 14
size_bytes: 1035300
sha256: 97d6c1f7ace90759f747887617412d42a09604c329fb2f3e77828c916b61a249
```

### D04

```text
document_id: D04
filename: sjim924.pdf
pages: 12
size_bytes: 272023
sha256: adff9e19beed02134a6f42596f6682f4177baca869ce6ab13cdb4980af9aebd0
```

### D05

```text
document_id: D05
filename: ufn289.pdf
pages: 22
size_bytes: 4577420
sha256: 5af84d6448e133aa2aae13a7f68c234e56840c64ebc84e02b66b7246ab0aca64
```

Total corpus:

```text
5 PDFs
98 pages
```

---

## 4. Ground Truth

Ground Truth is fixed. Structural validation passed, but the 2026-10-02 audit
found visual transcription/bbox discrepancies; see `reports/PROMPT12_CODE_AUDIT.md`.
Do not modify GT without explicit approval.

Total:

```text
40 GT objects
0 GT validation issues
```

Category counts:

```text
Text       9
Tables     7
Math       6
Chemistry  6
Images     5
Diagrams   7
```

All GT objects have:

- document_id
- page
- bbox
- category-specific reference content
- assets where required

Chemistry GT consists of:

```text
2 chemical_formula objects
4 chemical_structure objects
```

Do not modify Ground Truth, object IDs, bboxes, references, manifests, or hashes without explicit user approval.

Important GT-related locations include:

```text
ground_truth/
benchmark/object_manifest.json
```

The current benchmark intentionally uses representative objects rather than full-page/full-document exhaustive annotation.

---

## 5. Active 10 tools

### Local tools

1. PyMuPDF
2. pdfplumber
3. pdfminer.six
4. Docling
5. MinerU

### Cloud tools

1. OCR.Space
2. Nutrient Data Extraction
3. Mindee
4. Adobe PDF Extract
5. LlamaParse

### Inactive / reserve

Azure Document Intelligence is not part of the active 10-tool benchmark.

Marker is no longer part of the benchmark.

---

## 6. Marker status

Marker was previously one of the local parsers but was removed from the active benchmark because it was too slow and operationally problematic on Windows.

Observed historical behavior:

- Marker could run with llama.cpp after workarounds.
- D01 took roughly 46 minutes, not 1h45.
- Marker was replaced by `pdfminer.six`.

Current rule:

```text
DO NOT reintroduce Marker into the active benchmark.
```

Some legacy Marker files / environment / references may remain for audit:

```text
.venv-marker
src/.../marker_adapter.py
legacy tests
some historical probing code
```

These may be cleaned later, but do not spend benchmark effort on Marker unless explicitly asked.

Known legacy test issue:

```text
tests/test_heavy_local_adapters.py
tests/test_marker_standardizer.py
```

still import `MarkerAdapter`, even though Marker is not exported as an active adapter.

Current non-heavy test command:

```powershell
.venv\Scripts\python.exe -m pytest -q `
  -m "not heavy" `
  --ignore=tests/test_heavy_local_adapters.py `
  --ignore=tests/test_marker_standardizer.py
```

Most recent result:

```text
113 passed
```

---

## 7. Python environments and important versions

### Main `.venv`

Observed important versions:

```text
Python             3.12.10
PyMuPDF            1.28.2
pdfplumber         0.11.10
pdfminer.six       20260107
Docling            2.130.0
```

Docling environment also used:

```text
torch              2.14.0+cu130
torchvision        0.29.0+cu130
EasyOCR            1.7.2
transformers       5.8.0
```

`transformers==5.8.0` is intentional. A later 5.17 build caused a Granite Vision / `create_causal_mask` / `cache_position` crash.

### MinerU

```text
mineru[full]        4.0.0
```

MinerU config used:

```yaml
model:
  small_backend: torch
  vlm:
    engine: llama-cpp
```

lmdeploy / Triton was unsuitable on this Windows environment.

### Cloud environment

A separate `.venv-cloud` exists.

Observed packages include:

```text
mindee              5.3.1
nutrient-dws        3.1.0
llama-cloud         2.16.0
pdfservices-sdk     4.2.0
```

Do not expose or print API secrets.

---

## 8. Core benchmark pipeline

The benchmark pipeline uses a standardized internal document schema.

Conceptual flow:

```text
PDF
  -> adapter raw output
  -> standardized document
  -> normalized document
  -> GT matching
  -> object evaluation
  -> pair/tool aggregation
  -> raw/clean result datasets
```

Canonical cache structure:

```text
outputs/benchmark/cache/<tool>/<document_id>/
```

Typical cached stages include:

```text
raw/
raw_result_snapshot.json
adapter_result.json
run.json / adapter run metadata
standardized.json
normalized.json
matches.json
object_evaluation.json
pair_result.json
cache_manifest.json
```

Experiment bundles are written under:

```text
outputs/benchmark/experiments/
```

---

## 9. Cache safety / cloud API policy

This is one of the most important project rules.

### Never use automatically

```text
--force-api
```

unless the user explicitly approves a fresh paid/vendor call.

Do not silently re-call cloud APIs if a cached vendor raw response exists.

Do not re-run expensive local parsers unnecessarily.

Cloud behavior is designed so that compatible cached `standardized.json` can be reused, and stale vendor raw can be intentionally re-standardized without calling the vendor.

The final benchmark repair intentionally avoided new cloud API calls.

---

## 10. Mindee-specific behavior

Mindee trial limits required chunking.

A single source PDF may be split into multiple sequential API requests of at most 10 pages, then reassembled.

Chunking used:

```text
D01: 10 + 10 + 10 + 10 + 3
D02: 7
D03: 10 + 4
D04: 10 + 2
D05: 10 + 10 + 2
```

Important methodological statement for final reporting:

> One source PDF may be split into multiple Mindee API requests and reassembled, while page numbering and Ground Truth remain unchanged.

Do not treat this chunking as multiple documents.

---

## 11. LlamaParse fixes already applied

LlamaParse had several historical compatibility failures.

### Table dimension fix

Native payload fields such as `rows` / `columns` may be lists rather than integer counts.

A helper similar to `_dimension_count(...)` was added so table dimensions can be inferred safely.

### BBox fix

LlamaParse bbox payloads may be lists of dictionaries rather than only a flat `[x0, y0, x1, y1]`.

Support for list-of-dicts bbox was added.

### Stale failed metadata fix

Historical `adapter_result.json` / run metadata could still say `status=failed` even after valid cached standardization existed.

Runner logic was patched so stale historical failure metadata does not invalidate a compatible `reuse_standardized` / successful re-standardization path.

Final LlamaParse cached standardization is valid for all five documents.

Observed valid standardized counts:

```text
D01 pages=43 tables=14 text_blocks=424
D02 pages=7  tables=11 text_blocks=99
D03 pages=14 tables=8  text_blocks=146
D04 pages=12 tables=14 text_blocks=133
D05 pages=22 tables=11 text_blocks=416
```

Do not remove these compatibility fixes.

---

## 12. Text matching architecture and fixes

The original matching behavior was primarily one-to-one Hungarian assignment.

Important original configuration:

```text
spatial_min_iou       = 0.10
content_min_similarity = 0.25
```

Original behavior had a major granularity problem:

- GT text regions may be paragraph/region-sized.
- Some tools produce many small text blocks.
- If both GT and candidate had bboxes, matching used bbox IoU.
- A valid text region could therefore fail if extraction was fragmented.

A multi-block text matching fallback was added.

Current design intent:

1. Preserve original one-to-one Hungarian behavior.
2. For unmatched text GT regions, allow aggregation of multiple source text blocks spatially overlapping the GT region.
3. Reconstruct text in deterministic reading order.
4. Do not lower spatial thresholds merely to accept contaminated cross-column text.
5. Do not change non-text matching semantics.

Regression tests:

```text
tests/test_benchmark_matching.py
7/7 passed
```

Key test semantics:

- spatial Hungarian remains one-to-one
- content fallback works when bbox is missing
- unmatched prediction remains None
- repeated words at different coordinates remain in multi-block text
- identical text at identical bbox is deduplicated
- different text at identical bbox is preserved
- source words are not reused across GT regions

---

## 13. pdfplumber word-level fix

This was a major Prompt-12 correction.

Problem:

`pdfplumber.extract_text_lines()` could merge the left and right columns of a two-column page into one wide text line.

D05 page 10 / GT object `TXT_009` exposed this issue.

Old result:

```text
TXT_009 unmatched
```

Diagnosis showed candidate lines had bboxes crossing both columns and low overlap with the GT region.

Correct fix:

- preserve `extract_words()` output in pdfplumber raw JSON
- also keep `extract_text_lines()` for audit/backward compatibility
- standardization prefers word-level blocks
- multi-block text matcher reconstructs GT-region text from word bboxes

This avoids lowering the matcher threshold just to accept cross-column contamination.

Smoke result after the fix:

```text
D05 page 10 text blocks: 831
source: extract_words
TXT_009 matched=True
method=text_multiblock_overlap
blocks=135
match_score=1.0
```

Final benchmark:

```text
pdfplumber text GT matched: 9/9
pdfplumber Text Score: ~82.8974
```

The historical text score before the 2026-10-01 deduplication repair was
~73.6329. The repair preserves repeated words at distinct bboxes; TXT_009 now
contains 152 source blocks instead of the historical 135 shown above.

Do not revert pdfplumber to line-only standardized text.

---

## 14. pdfminer.six replacement

`pdfminer.six` replaced Marker in the active local roster.

Observed D02 smoke:

```text
status: success
pages: 7
wall time: ~2.0142 sec
CPU time: ~0.53125 sec
peak RSS: ~573.55 MB
GPU process: null
Torch GPU: 0
```

Raw metadata example:

```text
857 text blocks
5 images
0 vector figures
warnings=[]
```

One important observed outlier:

```text
pdfminer D05 ~12.132 sec/page
```

versus roughly:

```text
D01 ~0.043
D02 ~0.078
D03 ~0.041
D04 ~0.053
```

This is a large statistical outlier but was retained rather than automatically removed.

Do not silently drop outliers merely because they are large.

---

## 15. Docling

Docling fully completed D01-D05 and should not be rerun casually because it is expensive.

One warning on D05:

```text
MatchingPostProcessor dropped 3/179 cells
```

This was treated as a warning, not a failed run.

Docling is much more resource intensive than most tools.

Final approximate benchmark operational values:

```text
total_processing_time_sec ~7098.76
sec_per_page               ~72.44
peak_ram_mb                ~10878
peak_vram_mb               ~8180.7148
```

Do not confuse cached wrapper runtime with actual benchmark processing time.

---

## 16. MinerU

MinerU completed D01-D05 successfully.

Known warning:

```text
D05: output was truncated due to length limit
```

The run still completed successfully.

Final approximate VRAM:

```text
~4767.93 MB
```

Do not suppress this truncation warning; keep it as benchmark behavior.

---

## 17. VRAM metric bug and final semantics

A major Prompt-12 bug was found.

Old logic could use:

```text
torch_peak_reserved_mb
```

as the published `peak_vram_mb`.

For Docling this produced:

```text
16558 MB
```

which is impossible on an 8 GB GPU.

Observed Docling telemetry included:

```text
gpu_peak_process_mb      = None
gpu_peak_device_used_mb  = 8180.71484375
torch_peak_allocated_mb  = up to 11376.34
torch_peak_reserved_mb   = up to 16558
```

`torch_peak_reserved_mb` is allocator telemetry, not physical process-exclusive VRAM.

Final intended VRAM logic:

1. If attributable `gpu_peak_process_mb > 0`, use it.
2. Otherwise, only use `gpu_peak_device_used_mb` as a device-level upper bound when Torch telemetry proves actual CUDA activity for that adapter.
3. Otherwise report 0 MB.
4. Keep Torch allocated/reserved metrics only as diagnostics, not the published physical VRAM metric.

Final expected aggregate values:

```text
Docling ~8180.71484375 MB
MinerU  ~4767.93359375 MB
CPU / cloud-client tools: 0 MB unless attributable GPU activity exists
```

Do not regress this logic.

---

## 18. Chemistry semantics

Chemistry is a required benchmark category.

Internal schema uses dedicated `ChemicalObject`:

```text
subtype = linear_formula | structure
```

GT chemistry:

```text
2 linear chemical formulas
4 chemical structures
```

Current active adapters do not emit dedicated `ChemicalObject` predictions.

Therefore:

```text
Chemistry Score = 0
```

for all ten tools.

This is intentional under the benchmark's strict six-category policy.

Important interpretation:

> Chemistry Score = 0 means the active standardized adapter did not provide an evaluable chemistry-specific object in the benchmark schema. It does not necessarily prove that no chemistry-related information existed anywhere in the vendor/raw output.

Do not automatically map:

```text
math_formula -> chemical_formula
image -> chemical_structure
diagram -> chemical_structure
```

unless the benchmark methodology is explicitly changed.

Current scoring uses six equal category weights:

```text
Text       1/6
Table      1/6
Math       1/6
Chemistry  1/6
Image      1/6
Diagram    1/6
```

`strict_overall_categories=True`.

Unsupported required categories remain zero-scored rather than being omitted from Overall Score.

---

## 19. API cost semantics

API cost is included in result datasets.

Important interpretation:

- local tools: 0 / not applicable
- OCR.Space: 0 actual in the observed run
- several cloud tools may show 0 because trial/free/estimated cost was used

Do not interpret a zero cloud API cost as universal commercial market price.

Preserve cost provenance / caveats in reports.

---

## 20. Final Prompt-12 experiment

The current final clean benchmark experiment is:

```text
experiment_id: 20261003T202925Z_s42_ee4d6ac4
pipeline_version: prompt12-post-ocr-chemistry-cached-raw-v1
seed: 42
status: clean
```

This experiment re-standardizes all 50 pairs from compatible saved raw results,
then recomputes normalization, matching, evaluation and aggregation after all
confirmed Prompt 12 and OCR.Space repairs. Source experiment
`20261002T222803Z_s42_14c5cd35` and all 2,284 recorded legacy-cache files remain
unchanged. No adapter acquisition, model inference or vendor API was run again.
Historical attributable adapter time/resource/cost records remain the published
operational metrics; the current re-standardization measurements are retained
separately in each pair result. See `reports/PROMPT12_CONTROL.md` and the
experiment's `control_prompt12_summary.json`.

Final validation result:

```text
10 tool rows
50 tool-document pair rows
400 object rows
0 errors
12 warnings
all 10 tools clean
all 50 pairs successful
all 400 object results present
all 50 pair actions standardize_only
0 adapter acquisitions
0 cloud API calls
```

The final run was independently checked for:

- missing values
- failed runs
- duplicates
- impossible score ranges
- negative operational metrics
- score aggregation consistency
- page totals
- object totals
- pair aggregation consistency
- overall score consistency
- data integrity
- statistical outliers

All 623 structural, cache-provenance and score-reproduction checks passed.

Control Prompt-12 artifacts are stored under:

```text
reports/benchmark_control/20261003T202925Z_s42_ee4d6ac4/
```

Inside the experiment bundle, canonical result files include:

```text
results/raw_results_dataset.csv
results/clean_results_dataset.csv
results/pair_operational_dataset.csv
results/object_results_dataset.csv
results/quality_issues.csv
results/data_quality_summary.json
```

---

## 21. Final warnings

Final dataset contains 12 warnings, all statistical IQR outliers rather than data corruption.

Observed warning themes:

### Docling

- Diagram Score
- sec/page
- total processing time
- RAM
- VRAM

### MinerU

- Diagram Score
- RAM
- VRAM

### Other score outliers

- OCR.Space Text Score
- OCR.Space Chemistry Score
- pdfminer Table Score
- Mindee Table Score

These values were retained intentionally.

Rule:

```text
Do not delete statistical outliers automatically.
```

Investigate them, document them, and remove only with explicit methodological justification.

---

## 22. Important final validation invariants

Before treating any future modified dataset as valid, verify at minimum:

```text
tool rows = 10
pair rows = 50
object rows = 400
all pair statuses = success
all tools integrity = clean
failed_runs_count = 0
all tools total_pages = 98
matched_objects + missing_objects = 40 for every tool
all score columns in [0,100]
operational metrics >= 0
pair keys unique
tool/object keys unique
Overall Score = mean of six category scores
tool processing time = sum of pair processing times
tool peak RAM = max pair peak RAM
tool peak VRAM = max pair peak VRAM
tool API cost = sum pair API costs
```

Final Prompt-12 validation passed all of these.

---

## 23. Benchmark philosophy

Preserve these methodological principles:

1. Do not change GT to make tools look better.
2. Do not alter thresholds after looking at scores unless there is a demonstrated evaluator bug.
3. Separate extraction quality from evaluator / schema limitations.
4. Preserve raw vendor outputs and provenance.
5. Avoid duplicate paid API calls.
6. Treat outliers as observations first, not errors.
7. Do not compare tools using cached-wrapper runtime; use stored adapter processing metrics.
8. Keep transformations deterministic where possible.
9. Seed = 42 unless the experiment design explicitly changes.
10. Document every benchmark-affecting patch.

---

## 24. User working style relevant to this repository

The user prefers:

- explain the approach briefly before code
- avoid unnecessary complexity
- make as much of the technical work as possible automatically
- user should mainly run commands / provide files when local execution is required
- after each project stage, explicitly state what must be done manually
- do not ask the user to paste credentials
- do not casually rerun expensive or paid services

When giving PowerShell commands, make them copy-pasteable for Windows.

---

## 25. Current status

Prompt 12 acquisition, repair and cached official recomputation are complete.
The 2026-10-02 code/GT audit findings are fixed; see
`reports/PROMPT12_CODE_AUDIT.md` and `reports/PROMPT12_CONTROL.md`.

Repair phase: the user requests one issue per prompt unless explicitly asking
for several, no new research reports, and the remaining issue count at the end
of each response. A04 and MinerU's part of A05 were authorized together;
Docling's part of A05 and A06 were subsequently authorized together; A07 and
pdfminer's part of A08 were subsequently authorized together; PyMuPDF's part
of A08 and LaTeX command boundaries from A09 were subsequently authorized together.

Completed: Nutrient A01 coordinate scaling. The adapter uses native page canvas
dimensions for objects, cells and captions; legacy responses without dimensions
retain the PDF-unit fallback. Invalid/incomplete native dimensions are rejected.
Twelve regression cases were added; the safe suite then had 125 passing tests.
All five cached responses were re-standardized into isolated copies under
`outputs/repairs/nutrient_coordinates_v1/`: 1497 object boxes and 952 cell boxes
verified. Shared caches, GT and official scores remain unchanged. Cache
versioning is still pending; ordinary resume can still reuse old standardized
data. Do not claim that the published Nutrient results have been updated.

Completed: MinerU A02 string HTML/Markdown tables. Native string content now
produces cells; HTML row/column spans and empty cells are preserved. Markdown
requires a header separator, preserves escaped pipes and inline cell content,
and does not invent cells for ragged rows. Original Markdown is retained in
provenance; legacy HTML dictionary/top-level fields still work. No other
adapter or category was changed. Sixteen regression cases were added; the safe
suite then had 141 passing tests. Cached responses were re-standardized into
`outputs/repairs/mineru_tables_v1/`: all 34 tables (26 Markdown, 8 HTML), 800
cells and 31 merged cells verified. All non-table page data and table IDs,
bboxes, captions and reading order are unchanged. An isolated downstream
check gives Table macro 71.44213757602523 (baseline 7.651934503932734).
This is not a new official benchmark; shared caches and GT remain unchanged.

Completed: Adobe A03 indexed table roots. The adapter matches the complete
terminal Table or Table[n] path segment, including nested roots, while keeping
TR/TD descendants out of the table collection. Existing duplicate handling and
missing-CSV behavior are preserved. Fourteen regression cases were added;
the safe suite then had 155 passing tests. Five cached responses were
re-standardized into `outputs/repairs/adobe_table_paths_v1/`: 29 native tables
and 1062 CSV cells verified, versus 5 tables and 143 cells in the baseline.
All 24 previously skipped indexed roots are retained. Existing tables and
non-table content are unchanged; the original reading order is preserved
with the newly admitted table IDs inserted in native order. The 232 image
assets copied to the isolated directory have identical bytes. The isolated
Table macro check is 65.86282495882962 (baseline 28.156357993688108).
No API calls; GT, shared caches and the official experiment are unchanged.

Completed: Docling A04 picture classification. It now uses docling-core's
get_main_prediction(): maximum confidence, first prediction when confidences
are absent or tied. Explicit mapping native_top1_v1: bar/line/pie/other_chart,
box_plot and scatter_plot map to chart; flow_chart to flowchart;
engineering_drawing to scientific_scheme. Other/missing classes remain Image.
No confidence threshold was tuned and no GT label was used to choose a class.
Selected prediction and mapping version are retained in provenance. Existing
chemistry/table categories are not inferred from a picture classification.
Twenty-two regression cases cover native metadata, alternatives, missing/tied
confidence, and classification mapping. Offline re-standardization under
`outputs/repairs/docling_picture_classification_v1/` preserves all 47 pictures:
6 images and 41 diagrams (previously 0 and 47). All asset bytes, captions,
bboxes, reading order and non-visual data are unchanged. Isolated Image macro
is 44.52628592791049 (baseline 0); Diagram macro is 38.57480850556238 (baseline
37.908141838895716). Diagram child text remains a separate pending repair.

Completed: MinerU visual assets/captions (part of A05). String image_source and
legacy dictionary/filename representations are supported. Native captions
content/text is read in order, with singular legacy fields as fallback;
coordinates are not read as text and aliases are not duplicated. Shared caption
handling also restores table captions without changing cells or structure.
Eighteen regression cases cover image/chart files, missing files/directories,
caption order, legacy fallback and tables. Copies under
`outputs/repairs/mineru_visual_assets_v1/` retain the earlier table repair:
55 visual files verified byte-for-byte, 57 native caption records attached to
46 visual objects, and 44 table-caption records attached to 30 tables. All 800
table cells are unchanged. Image macro is 66.91894224834618 (baseline
37.748874221135296); Diagram macro is 9.289284927586925 (baseline
8.487201594253591). No network calls or model runs were made.

Completed: Docling picture child text (remaining A05). Native child references
are traversed in source order, including groups; captions and footnotes are
excluded. Separate pictures/tables retain their own content. Repeated labels
at different native IDs remain repeated; no semantic key_elements are inferred.
All picture text and source IDs are retained in provenance; DiagramObject also
receives text_elements. Normal document traversal/reading order is unchanged.
Five regression cases cover images, diagrams, repeated labels, captions,
footnotes, groups, nested pictures and empty children. Re-standardization in
`outputs/repairs/docling_picture_text_v1/` independently verifies all 846 native
text records: 820 for 35 diagrams, 26 retained in image provenance. All 47 visual
files, classifications, boxes, captions and non-visual page data are preserved.
Diagram macro is 40.19306332071985 versus 38.57480850556238 after A04.

Completed: text region alignment A06. Hungarian still assigns one-to-one anchors.
An anchor with bbox can be completed by free spatial fragments extending its
area inside the GT region; nested blocks alone do not justify completion.
Whole-region/page blocks and nonspatial anchors remain unchanged. Unmatched
regions retain the spatial fallback. Free fragments have one owner, selected
by the existing overlap metric, then IoU, then stable GT ID; no reference text
or evaluated score chooses aggregation. Both thresholds are unchanged. Exact
normalized-text/bbox duplicates are removed before assignment as well as during
aggregation, preventing duplicate aliases from serving different GT objects.
Repeated words at distinct coordinates remain. Sixteen regression cases cover
partial/full matches, ownership, duplicates, native order, cross-column
protection, custom thresholds and score-independent selection.
`outputs/repairs/text_region_alignment_v1/verify.py` verifies all 50 cached
pairs: 90 text objects, 310 unchanged non-text matches and unique source use.
Text document-macro checks on identical historical normalized inputs:
pymupdf 63.52919575 -> 91.62421191; pdfplumber unchanged 82.89743312;
docling 53.91418679 -> 92.00628907; pdfminer 40.73857345 -> 45.00030738;
mineru 52.11384740 -> 84.12326816; adobe_extract 53.85944314 -> 92.20227815;
llamaparse 61.41045182 -> 92.46081299. OCR.Space, Mindee and historical Nutrient
are unchanged. No text object worsened; 44 improved. A separate interaction
check with A01's corrected Nutrient copies gives 54.58874636 -> 93.73179967
(8 improved, 1 unchanged). The old-coordinate comparison is not a score for the
repaired Nutrient adapter. Parent/child duplication in pdfminer and Mindee's
page granularity still need their own adapter fixes.

Completed: Mindee word-level representation A07. Each readable native word is
now a separate TextBlock with its original normalized polygon, native index and
complete native word record in provenance. Native response order determines
reading order. The full page text remains in document metadata for audit/export
without creating a duplicate scored page block; a page-level block is retained
only when no readable words exist. Missing polygons remain bbox=None. Twelve
regression cases cover all supported wrappers/aliases, list/dict polygons,
repeated words, missing geometry, empty pages, page numbering and two-column
regional matching. Offline re-standardization in `outputs/repairs/mindee_words_v1/`
independently verifies all 35,674 native words and all 98 page texts. Non-text
matches are unchanged. With the A06 matcher, isolated Text macro is
93.50514992125817 versus 19.775903025182444; all nine GT text objects improve.

Completed: pdfminer parent/child text hierarchy (part of A08). New raw output
records explicit parent_text_index relationships. For legacy raw caches, a
parent is suppressed only when a contiguous spatially-contained line prefix in
native preorder reproduces its complete normalized text. Text equality alone
does not establish hierarchy; incomplete/unproven parents remain. Repeated text
at different coordinates remains repeated. Sixteen regression cases cover
horizontal/vertical text, explicit and legacy hierarchy, non-adjacent explicit
children, nesting, repeated text, incomplete parents and invalid indices.
Offline re-standardization under `outputs/repairs/pdfminer_text_hierarchy_v1/`
removes 7,191 fully represented parents, retains 73,001 native lines and verifies
54 copied assets byte-for-byte. All non-text matches are unchanged. With A06,
isolated Text macro is 81.65844449932783 versus 45.000307378531055: seven GT
objects improve, TXT_005 is unchanged, and TXT_009 decreases 16.54411764705882
to 15.781922525107609. This worsening is retained as an observed result rather
than tuned away.

Completed: PyMuPDF span/line boundaries (remaining A08). Standardization now
emits one TextBlock per native line and concatenates its native font/style spans
without inventing spaces; real whitespace already present in span text remains.
Line bbox is preferred, with span-union and parent-block fallbacks. Native
block/line order from get_text(sort=True) is retained instead of applying a
second coordinate sort, which had reordered glyph fragments in a complex
formula. Span records, direction, writing mode and source indices are retained
in provenance. Text lines from one former native block share its old order_index,
so image/table order indices and all non-text predictions remain unchanged.
Eleven regression cases cover split words, real spaces, line granularity,
geometry fallbacks, empty/repeated/vertical lines, columns, hyphens and native
order. Offline re-standardization in `outputs/repairs/pymupdf_text_lines_v1/`
verifies 8,056 lines, including 2,668 multi-span lines, and 630 assets byte for
byte. All 31 non-text matches are unchanged. With A06, isolated Text macro is
99.98282967032966 versus 91.62421191473406: seven GT objects improve and none
worsen. TXT_002 is 99.8282967032967 because its reference contains a paragraph
break where the native extraction has a line wrap; this is not corrected by
guessing paragraph semantics.

Completed: LaTeX control-word boundaries (first part of A09). Whitespace after
a control word is retained as exactly one lexical delimiter only when the next
token begins with an ASCII letter; elsewhere the previous insignificant-space
policy remains. Unicode Greek conversion inserts the same delimiter when needed,
so alpha followed by x cannot become the different command alphax. Known and
unknown commands are treated lexically without a command allowlist. Eighteen
regression cases cover commands, Greek glyphs, braces, numbers, other commands,
protected text arguments and distinct longer command names. Offline validation
under `outputs/repairs/latex_command_boundaries_v1/` checks all 1,507 cached
formulas: 239 contain relevant source boundaries and 195 normalized values
change. Math scores are deliberately deferred until raw and normalized formula
payloads are separated in the next A09 repair. The scan also found five formulas
where the older ordering of spacing-command removal and whitespace compaction is
not idempotent; this is tracked as a new separate repair rather than silently
expanding this turn.

Completed: raw/normalized mathematical payload separation (remaining A09).
Matching now keeps the adapter's original `latex` (or `raw_text` fallback) in
the prediction and consults `normalized_latex` only for content matching.
Evaluation accepts raw and normalized representations separately: raw exact
match can no longer receive normalized prediction text, while normalized exact,
token F1 and edit similarity use the explicit normalized field when present.
Three end-to-end regressions cover matching, metric and framework behavior.
The isolated scan in `outputs/repairs/math_payload_cache_fingerprints_v1/`
re-evaluates all 60 tool/math-object rows from cached standardized documents:
19 are matched and no historical object score changes solely from this repair,
because none of those matches received the specific false raw-exact credit.
This negative result is retained rather than presented as a score improvement.

Completed: stage-aware, versioned cache invalidation (A10). Cache manifests now
carry independent SHA-256 fingerprints for raw acquisition, standardization,
normalization and evaluation. Raw fingerprints cover PDF/config and an explicit
per-tool acquisition version; standardization combines per-tool versions with
the adapter/common schema source hash; normalization and evaluation combine
source, configs, GT and object-manifest provenance as appropriate. A legacy
manifest can validate its raw response from the recorded PDF/config hashes, but
cannot validate standardized/normalized/evaluated derivatives. Runner output is
written under `cache/<tool>/<document>/versions/<fingerprint>/`; a standardizer
change reads the compatible legacy `raw_result_snapshot.json` and raw directory
while writing the new standardized/assets/downstream files to that separate
version. The legacy cache is not overwritten. Normalization/evaluation changes
can copy a compatible standardized document into another version without
running an adapter. A changed raw acquisition input remains a stricter case.
The dry verification in
`outputs/repairs/math_payload_cache_fingerprints_v1/cache_plan.csv` covers all
50 pairs: 50 compatible raw caches, 50 invalidated legacy standardized caches,
50 planned `standardize_only` operations, zero adapter acquisitions and zero
cloud API calls. The worker regression independently verifies reading raw from
one cache and writing standardized output to another.

Completed: LaTeX spacing-command idempotence. A single TeX control-space is now
removed before named spacing commands and general whitespace compaction, without
touching doubled-backslash row separators. Simple sub/superscript atoms may have
insignificant leading whitespace, and a text-like command plus its balanced
argument is treated as one atom. This prevents `\ ,` from becoming `\,`, `\ j`
from becoming `\j`, and `_ \mathrm{...}` from becoming canonicalizable only on
the second pass. All 1,507 cached formula objects were checked (1,319 contain a
LaTeX payload): zero remain non-idempotent.

Completed: GT fractional-operator indices for MATH_002–004. The user's request
to proceed with these two listed items authorized this specific GT correction.
Visual evidence for all three objects shows a left subscript `t`, with no left
superscript. The exact prefix `{}_{0}^{t}D` was therefore replaced with
`{}_{t}D` in both `ground_truth/objects.json` and `objects.jsonl`. IDs, pages,
bboxes, all other reference text and `benchmark/object_manifest.json` are
unchanged. The verification reconstructs both original GT file SHA-256 values
by reversing exactly these three replacements. JSON and JSONL remain equal and
the full manifest validation passes. An isolated 10-tool x 3-object comparison
has 9 changed scores: 5 improve and 4 worsen; all are retained. Artifacts are in
`outputs/repairs/latex_idempotence_gt_indices_v1/`. No official experiment was
recomputed.

Completed: GT unit alignment for MATH_005 and MATH_006. The user's request to
proceed with the next two items authorized these two specific GT changes.
MATH_005 now contains only numbered display equation (6): its bbox excludes the
separate Mittag-Leffler definition and intervening prose, and its reference is
the displayed `u(x,t)` equation. MATH_006 retains its bbox and now contains the
complete seven-line numbered equation (57), including the expressions for
`E_1` and both cases of `n_{21}`. Object IDs, documents, pages, categories and
`benchmark/object_manifest.json` are unchanged. Reversing exactly these two
object replacements reconstructs both prior GT file SHA-256 values; JSON and
JSONL are equal and full manifest validation passes. The isolated cached
10-tool x 2-object comparison has 6 changed scores: 5 improve and 1 worsens;
all are retained. Artifacts are in `outputs/repairs/math_gt_units_v1/`. No
official experiment was recomputed.

Completed: CHEM_001 bbox. Its old bbox clipped both ends of `FeCl3·6H2O`.
The replacement `[0.292984, 0.146948, 0.386878, 0.165088]` is the native PDF
word bbox and contains only the complete formula. JSON and JSONL remain equal,
the manifest is valid and reversing this single object restores the previous
GT file hashes. All 10 cached Chemistry scores remain zero because active
adapters still emit no typed `ChemicalObject`; this expected non-effect was
verified rather than used to select the bbox.

Completed: visual detection on partially annotated pages. The default matching
protocol is now explicitly `sampled_gt_recall_v1`: required GT misses receive
zero object scores, while unmatched candidates are retained as diagnostic
`unmatched_candidate_count` and are not called false positives. The separate
`exhaustive_page_f1_v1` mode retains page-level FP/F1 for a future fully
annotated page. On the historical cached normalized documents, all 120 visual
assignments are unchanged. The legacy protocol had 21 object contexts with
`fp>0`; 19 scores change after removing the unsubstantiated penalty, all upward,
and no score worsens. Artifacts are in
`outputs/repairs/chem_bbox_visual_detection_v1/`. No official experiment was
recomputed.

Completed: CHEM_002 bbox. Its old bbox clipped the `Fe` prefix of
`FeSO4·(NH4)2SO4·6H2O`. The replacement
`[0.29518, 0.163128, 0.49268, 0.181268]` is the native PDF word bbox and
contains the complete formula only. JSON and JSONL remain equal, the manifest
is valid and reversing this single object restores the previous GT file hashes.
All 10 cached Chemistry scores remain zero because active adapters emit no
typed `ChemicalObject`. Verification artifacts are in
`outputs/repairs/chem002_bbox_v1/`.

The safe suite now has 293 passing tests. The descriptions above retain the
history of isolated repair checks. Their fixes are now incorporated into the
official experiment `20261002T222803Z_s42_14c5cd35`.

Remaining: 0 confirmed repair items from the Prompt 12 audit sequence.

Completed: cached raw re-standardization and control Prompt 12. All 50 pairs
used `standardize_only` with zero adapter acquisitions and zero cloud API calls.
The new clean experiment contains 10 tool rows, 50 pair rows and 400 object
rows, with zero errors and 11 retained IQR warnings. The control independently
reproduced matching, all object scores, aggregate scores and confidence
intervals; 623/623 checks passed. All 2,284 historical legacy-cache files are
byte-identical to the hashes recorded by the previous baseline. Artifacts are
in `reports/benchmark_control/20261002T222803Z_s42_14c5cd35/`, and
`outputs/benchmark/latest_experiment.txt` now selects the new experiment.
An initial attempt, `20261002T222351Z_s42_0e9010fc`, exposed stale absolute
machine paths in legacy raw snapshots and was preserved as `aborted`. Worker
standardization now rebases raw artifact/saved-directory paths to the current
cache without modifying the snapshot; `worker.py` is included in the
standardization fingerprint and a regression test covers repository moves.

Chemistry's strict typed-output zero and OCR.Space's region/canvas limitations
are documented constraints, not additional confirmed code defects in this count.
Further GT or methodological changes still require explicit approval under
AGENTS.md.

Current state:

```text
Corpus fixed                  DONE
GT fixed / structural checks  PASS; confirmed audit discrepancies repaired
10 adapters benchmarked       DONE
50 tool-document pairs        DONE
400 object evaluations        DONE
Text region representation    FIXED for confirmed adapter/matcher defects
Repeated-word deduplication    FIXED (2026-10-01)
pdfplumber column bug         FIXED
LlamaParse compatibility      FIXED
LlamaParse stale metadata     FIXED
VRAM publication bug          FIXED
Chemistry semantics           DECIDED
Final dataset integrity       PASS
Math raw/normalized payload   FIXED
LaTeX normalization fixed pt  FIXED on all 1,507 cached formula objects
MATH_002–004 GT indices       FIXED; exact three-reference diff recorded
MATH_005 GT unit              FIXED; single numbered equation (6)
MATH_006 GT extent            FIXED; complete numbered equation (57)
CHEM_001 GT bbox              FIXED; native complete formula word bbox
CHEM_002 GT bbox              FIXED; native complete formula word bbox
Visual detection protocol    FIXED; sampled-GT recall semantics
Stage cache invalidation      FIXED; versioned downstream run complete
Prompt 12 confirmed repairs   COMPLETE; official recomputation validated
Prompt 13                     COMPLETE for current official baseline
```

Do not repeat acquisition to fix downstream defects. Use preserved raw data,
versioned re-standardization and the repair sequence in the audit report.

---

## 26. What to do next

Prompt 13 is complete for the current official baseline
`20261003T202925Z_s42_ee4d6ac4`. Its entry point is
`reports/PROMPT13_ANALYSIS.md`; complete reproducible artifacts are under
`reports/statistical_analysis/20261003T202925Z_s42_ee4d6ac4/`. The analysis contains 24 CSV
tables, 17 figures in PNG/SVG, HTML/Markdown reports and a 17-page PDF atlas.
Preparation passed 601/601 checks, artifact verification passed 50/50 checks
and the safe non-heavy suite passed 305 tests. No adapters, models or cloud APIs
were run. Reports for earlier experiments remain historical.

Analysis code remains `scripts/analyze_benchmark.py`,
`analysis_statistics.py`, `plot_benchmark_analysis.py`,
`render_benchmark_analysis.py` and `verify_prompt13_artifacts.py`. Reproduction
uses the isolated `.venv-analysis` environment and
`requirements-analysis-pinned.txt`.

Interpretation limits remain: five fixed PDFs, unequal category coverage,
chemistry in one PDF, no Overall CI, exploratory paired CIs without
multiplicity adjustment, zero recorded costs rather than current tariffs, and
different cloud/local timing and resource scopes. Operational totals retain the
historical attributable acquisition/parser measurements; current
re-standardization measurements are stored separately in pair results.

Prompt 14 can use this refreshed analysis and its verified artifacts.

---

## 27. First actions for Codex

When opening this repository for the first time:

1. Read:
   - `CODEX_CONTEXT.md`
   - `AGENTS.md`
2. Inspect:
   - `git status`
   - `git diff`
   - repository tree
3. Locate the latest benchmark experiment:
   - `outputs/benchmark/latest_experiment.txt`
   - `outputs/benchmark/experiments/20261003T202925Z_s42_ee4d6ac4*`
4. Confirm active adapter roster is exactly 10 tools listed above.
5. Confirm Marker is not active.
6. Run only the safe non-heavy test suite unless the user explicitly asks for heavy integration testing.
7. Do not invoke cloud APIs automatically.
8. Do not modify Ground Truth automatically.
9. Report what you found before making broad architectural changes.

This repository contains expensive cached experiment data. Treat it as a reproducibility asset, not disposable build output.

---

## 28. Post-Prompt-13 OCR.Space text and chemistry repairs (2026-10-03)

The first two requested OCR.Space repair items are complete:

1. `TextOverlay.Lines` now produces line-level text blocks with normalized
   bboxes. Cached Engine 3 PDF coordinates fit a 2-pixel-per-PDF-point canvas on
   all 98 pages; the scale and raw pixel bbox are recorded in provenance.
2. When overlay is absent, `ParsedText` is split on blank lines and malformed
   giant paragraphs are bounded to 4,000 characters, independently of GT.

Implementation: `src/pdf_benchmark/adapters/cloud/ocr_space_adapter.py`.
Regression coverage: `tests/test_ocr_space_text_blocks.py`. Verification:
`scripts/verify_ocr_space_text_repairs.py` and
`outputs/repairs/ocr_space_text_blocks_v1/`.

The isolated five-document result changes OCR.Space from 98 whole-page text
blocks to 6,110 blocks: 5,929 spatial overlay lines and 181 fallback segments.
Eight of nine text GT objects match instead of six. Text Score changes from
25.8725 to 59.2612 and Overall from 11.9970 to 17.5617. D04/TXT_008 remains
unmatched because the saved provider response contains 84,766 characters and
no overlay regions. Non-text standardized content is unchanged.

All five cached raw pairs remain compatible and plan `standardize_only`; cloud
API calls and adapter acquisitions were zero. Hashes of 134 protected cached and
official files were unchanged by the isolated probe. The repair is now included
in official experiment `20261003T202925Z_s42_ee4d6ac4` and in its refreshed
Prompt 13 analysis.

The next two user-authorized chemistry tasks are also complete:

1. Strict compound-formula syntax in `ParsedText` now creates typed
   `ChemicalObject(subtype="linear_formula")` entries. The recognizer requires
   valid element symbols, at least two distinct elements and a numeric index.
   Encoded structure links are excluded. OCR lookalikes are used only to detect
   the source span; raw OCR text is preserved and no chemical correction is
   applied.
2. Native Markdown CodeCogs links containing an explicit `\\chem{...}` command
   now create `ChemicalObject(subtype="structure")` entries. The molecular
   expression and renderable provider URI are retained. Table-cell bboxes are
   reconstructed from provider row names and formula labels in
   `TextOverlay`, independently of GT.

Implementation remains in
`src/pdf_benchmark/adapters/cloud/ocr_space_adapter.py`. Regression coverage is
in `tests/test_ocr_space_chemistry.py`. Offline verification is in
`scripts/verify_ocr_space_chemistry_repairs.py` and
`outputs/repairs/ocr_space_chemistry_objects_v1/`.

Across all five cached documents the adapter emits 35 strict linear formulas
and 4 explicit structures. All four structures have provider-derived spatial
bboxes and remote renderable formula URIs. On the six D02 Chemistry GT objects,
matches change from 0/6 to 6/6. The isolated OCR.Space Chemistry Score is
64.0571 and its Overall after both post-Prompt-13 repairs is 28.2379, versus
Chemistry 0 and Overall 17.5617 after the text-only repair. These values are now
confirmed in the official cached experiment. All five raw caches remain
compatible and plan `standardize_only`; cloud/API acquisitions are zero. Non-chemistry
standardized content and evaluations are unchanged, and 204 protected files
are byte-identical.

Completed: separate chemistry detection and structured-extraction reporting.
`chemistry_detection_score` uses every Chemistry GT object and follows the
benchmark macro chain: document macro, with equal weight for linear formulas
and structures inside each document. `chemistry_structured_extraction_score`
uses matched chemistry objects only; it measures formula content for linear
objects and the renormalized IoU/label/extracted-representation components for
structures. If a tool detects nothing, structured extraction is N/A and the
aggregate record is omitted. Both scores are diagnostic and do not replace or
change the existing Chemistry Score or Overall.

The Prompt 13 analysis pipeline now emits
`chemistry_detection_extraction.csv`, independently reproduces both reporting
scores from object/metric rows and includes their table in Markdown/HTML. The
clean benchmark dataset exposes both optional columns. On the isolated repaired
OCR.Space result, Detection is 100.0 (6/6) and conditional Structured
Extraction is 61.6905. The combined Chemistry Score remains 64.0571 and Overall
remains 28.2379. The pre-repair result reports Detection 0 and Structured
Extraction N/A. Evidence is in
`outputs/repairs/ocr_space_chemistry_objects_v1/`.

The safe non-heavy suite now passes 305 tests. No proposed post-Prompt-13 repair
items remain.

---

## 29. Current cached recomputation and control Prompt 12 (2026-10-03)

The current official experiment is `20261003T202925Z_s42_ee4d6ac4`. All 50
tool-document pairs used `standardize_only` with compatible saved raw results.
Adapter acquisitions, local parser/model reruns and cloud API calls were zero.
The dataset is clean: 10 tool rows, 50 pair rows, 400 object rows, zero errors
and 12 retained IQR warnings. `outputs/benchmark/latest_experiment.txt` selects
this experiment.

Control Prompt 12 passed 623/623 checks. It independently reproduced matching,
all object evaluations, aggregate category/Overall scores and the chemistry
detection/extraction diagnostics. All 2,284 historical legacy-cache files are
byte-identical. Evidence is in
`reports/benchmark_control/20261003T202925Z_s42_ee4d6ac4/`, with entry point
`reports/PROMPT12_CONTROL.md`.

Compared with `20261002T222803Z_s42_14c5cd35`, 14 OCR.Space object scores
changed, all upward: eight text objects and six chemistry objects. OCR.Space
Text is 59.2612, Chemistry is 64.0571, Detection is 100.0, conditional
Structured Extraction is 61.6905 and Overall is 28.2379. Scores of the other
nine tools are unchanged.

An intermediate attempt, `20261003T202119Z_s42_eca66135`, exposed a cache
transport defect: `reuse_standardized` copied the JSON stage without its local
visual assets. It is preserved as `needs_attention` and superseded. The runner
now copies the assets tree with reusable standardized output, and this behavior
is included in the standardization fingerprint. The safe non-heavy suite passes
305 tests.

Prompt 13 was refreshed for this experiment. Its analysis contains 24 CSV
tables, 17 figures in PNG/SVG, HTML/Markdown reports and a 17-page PDF atlas.
Preparation passed 601/601 checks and artifact verification passed 50/50 checks.
The entry point is `reports/PROMPT13_ANALYSIS.md`; artifacts are under
`reports/statistical_analysis/20261003T202925Z_s42_ee4d6ac4/`.

---

## 30. Shared GT-independent chemistry detection and current baseline (2026-10-04)

The user authorized a raw-output audit for all ten tools and a common
GT-independent chemistry detector. The shared enrichment lives in
`src/pdf_benchmark/standardization/chemistry.py` and is invoked for both fresh
adapter conversions and cached `standardize_only` runs. The standardization
fingerprint includes this package.

Eligible evidence is deliberately narrow: strict compound-formula syntax,
explicit structural-formula table semantics, native images under that header,
or spatial atom/group labels within the explicitly identified structure
column. Ground Truth is not used by enrichment, raw formula spelling is
preserved, and generic math/image/diagram objects are not silently retyped.
The OCR.Space native ParsedText/CodeCogs path now shares the same linear syntax
recognizer and remains authoritative when it already produced a subtype.

The raw-cache audit is reproducible with
`scripts/verify_shared_chemistry_detection.py`; evidence is in
`outputs/repairs/shared_chemistry_detection_v1/`. It verified 50/50 compatible
raw pairs, 50 planned `standardize_only` actions, zero adapter acquisitions,
zero cloud calls, unchanged protected inputs/caches/official outputs, and
unchanged non-chemistry evaluations. The enriched corpus contains 355 chemical
objects: 315 linear formulas and 40 structures. All ten tools match all six
Chemistry GT objects. Detection is 100 for every tool; conditional Structured
Extraction ranges from 39.03599 to 75.12507.

The current official experiment is `20261004T013334Z_s42_20904978`, selected by
`outputs/benchmark/latest_experiment.txt`. All 50 pairs used
`standardize_only`; Docling/MinerU parsers and vendor APIs were not rerun. The
dataset is clean: 10 tool rows, 50 pair rows, 400 object rows, zero errors and
13 retained IQR warnings. Compared with `20261003T202925Z_s42_ee4d6ac4`, 54
object scores changed, all upward and all Chemistry rows for the nine tools
that previously had no typed chemistry output; OCR.Space and every
non-chemistry score are unchanged.

Control Prompt 12 passed 623/623 checks. Evidence is in
`reports/benchmark_control/20261004T013334Z_s42_20904978/`; entry point:
`reports/PROMPT12_CONTROL.md`. Prompt 13 was refreshed for this baseline and is
under `reports/statistical_analysis/20261004T013334Z_s42_20904978/`: 24 CSV tables, 17
figures in PNG/SVG, HTML/Markdown, and a 17-page PDF atlas. Preparation passed
601/601, artifact verification passed 50/50, and the safe non-heavy suite passes
308 tests. Entry point: `reports/PROMPT13_ANALYSIS.md`.

---

## 31. GT-independent math extraction repair (2026-10-04)

The math audit confirmed that pdfminer.six, Mindee, PyMuPDF and pdfplumber
preserved the sampled equations only as positioned text, while their adapters
emitted no `Formula` objects. OCR.Space retained explicit LaTeX in `ParsedText`
but did not standardize it as math. LlamaParse split the complete seven-line
equation (57) into separate formula objects. Adobe Extract returned all six
sampled formulas as `Figure` assets without a formula payload. Nutrient's
MATH_003 native formula payload is empty, and Docling's MATH_006 LaTeX is
provider-corrupted.

`src/pdf_benchmark/standardization/math.py` now provides a shared,
GT-independent fallback. It preserves explicit provider LaTeX and groups
consecutive display expressions only when the run is explicitly numbered. For
positioned plain text it detects right-side `(n)` equation markers, respects
columns and retains the raw parser/OCR text without pretending to reconstruct
LaTeX. The enrichment runs after chemistry for both fresh conversions and
cached `standardize_only`; its code is included in the standardization
fingerprint. OCR.Space additionally reads explicit display LaTeX directly from
its native `ParsedText` before overlay-only text standardization loses it.

Isolated evidence is in `outputs/repairs/math_extraction_v1/` and is
reproducible with `scripts/verify_math_extraction_repair.py`. All 50 cached raw
pairs succeeded with `standardize_only`; cloud calls were zero. All 70 non-math
category rows are unchanged. Math Score changes are: PyMuPDF 0 -> 21.9571,
pdfplumber 0 -> 19.8512, pdfminer.six 0 -> 20.9736, OCR.Space 0 -> 47.1481,
Mindee 0 -> 14.3851 and LlamaParse 30.1059 -> 49.3334. Docling, MinerU and
Nutrient are unchanged; Adobe remains zero because no structured formula
payload exists. Nine tools match 6/6 sampled math objects; Adobe matches 0/6.

The repair is now included in official experiment
`20261004T095716Z_s42_2334c932`, selected by
`outputs/benchmark/latest_experiment.txt`. All 50 pairs used
`standardize_only`; adapter acquisitions and cloud API calls were zero. The
dataset is clean: 10 tool rows, 50 pair rows, 400 object rows, zero errors and
13 retained IQR warnings. Compared with `20261004T013334Z_s42_20904978`, 31
object scores changed, all upward and all in Math; the other 369 object scores
are unchanged.

Control Prompt 12 passed 625/625 checks. It independently reproduced matching,
object evaluation, aggregation and confidence intervals; checked the exact
Math Score values and GT-independent provenance of enriched formulas; and
verified that 2,284 legacy-cache files are byte-identical. Evidence is in
`reports/benchmark_control/20261004T095716Z_s42_2334c932/`; entry point:
`reports/PROMPT12_CONTROL.md`. Prompt 13 has been refreshed for the same
baseline. Its entry point is `reports/PROMPT13_ANALYSIS.md`; reproducible
artifacts are under `reports/statistical_analysis/20261004T095716Z_s42_2334c932/`: 24 CSV
tables, 17 figures in PNG/SVG, HTML/Markdown reports and a 17-page PDF atlas.
Preparation passed 601/601 checks, artifact verification passed 50/50 checks,
and the safe non-heavy suite passes 314 tests. The report generator now derives
category leaders and the complete nonzero Math roster from the current data;
its profiles no longer contain prose inherited from the pre-math baseline.

---

## 32. Prompt 14 error analysis (2026-10-04)

Prompt 14 is complete for official experiment
`20261004T095716Z_s42_2334c932`. The deterministic selection takes the
lowest-scoring object in each tool/category pair, with object ID as the tie
breaker. This yields 60 examples: six categories for each of ten tools, which
exceeds the requested minimum of five examples per tool without creating a new
aggregate score.

Every example contains a rendered source PDF crop, Ground Truth, the saved
prediction, nearby output of another type when the typed prediction is absent,
an error classification, causal explanation, official Object Score and metric
components. The artifacts include 21 unique source crops and 14 copied
prediction assets. They distinguish complete misses from tables represented as
text, formulas represented as figures, diagrams represented as generic images
or Markdown, and spatially matched objects with empty payloads.

The entry point is `reports/PROMPT14_ERROR_ANALYSIS.md`; reproducible artifacts
are under `reports/error_analysis/20261004T095716Z_s42_2334c932/`, with archive
`reports/error_analysis/20261004T095716Z_s42_2334c932_error_analysis.zip`.
All 60 scores match the official object dataset, input hashes are unchanged,
adapter/parser executions and network calls are zero, artifact verification
passed 25/25 checks, and the safe non-heavy suite passes 314 tests.

---

## 33. Prompt 15 full technical report (2026-10-04)

Prompt 15 is complete for official experiment
`20261004T095716Z_s42_2334c932`. The report contains the requested 20-section
academic structure, metric formulas, quality/performance/cost tables, all 17
Prompt 13 figures, fixed tool versions, and links to official documentation.

Section 17 contains exactly five diagnostic examples for each of ten tools (50
total). They are selected deterministically as the five lowest Object Scores
among the six category-stratified Prompt 14 cases for each tool; ties use the
fixed category order and object ID. This selection is illustrative and is not
an additional score or ranking. Each case retains the source crop, Ground
Truth, saved output, cause and metric impact. The complete 60-case analysis
remains in Prompt 14.

Entry point: `reports/PROMPT15_FULL_REPORT.md`. Reproducible artifacts are in
`reports/final_report/20261004T095716Z_s42_2334c932/`; the ZIP is
`reports/final_report/20261004T095716Z_s42_2334c932_full_report.zip`.
Artifact verification passed 29/29 checks and the safe non-heavy suite passes
314 tests. PDF, GT, object manifest, caches and official scores are unchanged;
adapter acquisitions, heavy parser executions and cloud calls are zero.

---

## 34. Prompt 16 web application architecture (2026-10-04)

Prompt 16 is complete as an architecture-only deliverable. The entry point is
`reports/PROMPT16_WEB_ARCHITECTURE.md`; the full design is
`reports/web_architecture/WEB_APPLICATION_ARCHITECTURE.md`.

The proposed runtime uses a stateless FastAPI API, PostgreSQL as the job state
source of truth, Redis/Celery for asynchronous delivery, S3-compatible
temporary object storage, isolated worker pools for classic CPU, Docling GPU,
MinerU GPU and cloud adapters, and a React/TypeScript frontend with PDF.js.
It reuses the adapters and `StandardizedDocument`, while explicitly isolating
user jobs from Ground Truth, benchmark evaluation and benchmark caches. The
design covers streaming/multipart uploads, large PDFs, status transitions,
TXT/JSON exports, object previews, API keys, retries, cancellation, resource
limits, logging/tracing, TTL cleanup, security, single-server Docker Compose
deployment and a staged implementation plan. No application code was added,
no parser or vendor API was run, and protected benchmark artifacts are
unchanged.

---

## 35. Prompt 17 FastAPI backend (2026-10-04)

Prompt 17 is implemented under `src/pdf_benchmark/web/`; the entry point and
operational notes are in `reports/PROMPT17_FASTAPI_BACKEND.md`.

The REST API provides upload, converter discovery, asynchronous job creation,
status, structured result, original PDF, TXT/JSON downloads, deletion and
healthcheck. It has Pydantic settings/schemas, streaming size enforcement,
PyMuPDF validation, page limits, safe display filenames and UUID storage keys,
structured JSON errors/logs, TTL cleanup, atomic file records and subprocess
timeouts. The subprocess runner reuses `scripts/benchmark_worker.py` and the
existing pinned adapter environments, but writes only to the isolated web job
workspace. It disables implicit `.env` loading inside web workers and passes
only the selected provider's required credential variables. Cloud conversion
is disabled by default.

The current implementation is the single-node backend stage: a bounded thread
dispatcher launches isolated adapter subprocesses and atomic JSON records hold
metadata. The REST/service boundaries are ready for the Prompt-16 production
replacement with Celery/Redis, PostgreSQL and S3/MinIO. Multiple API replicas
and public unauthenticated deployment are not supported by this local runtime.

Focused web tests pass 7/7, including a real PyMuPDF adapter subprocess. The
safe non-heavy suite passes 321 tests. Docling, MinerU and vendor APIs were not
run. Benchmark inputs, Ground Truth, caches, official scores and the selected
baseline are unchanged.

---

## 36. Prompt 18 web interface (2026-10-05)

Prompt 18 is implemented as static frontend files under
`src/pdf_benchmark/web/static/`, served by FastAPI at `/`. The entry point is
`reports/PROMPT18_WEB_INTERFACE.md`.

The dependency-free browser UI provides PDF upload, converter selection,
disabled controls while a job is active, polling status/loading/error states,
processing time, source-PDF preview, Text/Tables/Formulas/Images/Raw JSON tabs,
and TXT/JSON downloads. It uses no CDN/npm dependency and exposes no provider
key. A protected `GET /api/v1/jobs/{job_id}/assets/{asset_path}` endpoint was
added for visual assets; traversal is rejected and assets remain inside the
completed job's `adapter/assets` directory.

Focused UI/API tests pass 8/8 and the safe non-heavy suite passes 322 tests.
The PyMuPDF end-to-end adapter check still passes; Docling, MinerU and cloud
APIs were not run. Benchmark inputs, Ground Truth, caches, official scores and
the selected baseline are unchanged.

---

## 37. Prompt 19 Docker and deployment (2026-10-05)

Prompt 19 adds CPU and GPU container deployment material without altering the
benchmark or starting adapters. `Dockerfile` packages lightweight CPU
converters and an isolated cloud-client venv; `Dockerfile.gpu` adds Docling and
MinerU with CUDA 12.6 / Ubuntu 24.04. `compose.yaml` provides one single-node
API runtime, healthcheck, JSON log retention, persistent temporary runtime
volume and optional Caddy HTTPS profile. `compose.gpu.yaml` forwards NVIDIA GPU
resources, persists model cache and limits execution to one worker.

The operational instructions and server-secret template are at
`reports/PROMPT19_DOCKER_DEPLOYMENT.md` and `deploy/.env.server.example`.
Docker build context excludes credentials, benchmark inputs, caches and
outputs. Static Compose validation passed; Docker Engine was not running, so no
image was built and no heavy parser or cloud adapter ran. Focused web tests pass
8/8 and the safe non-heavy suite passes 322 tests.

---

## 38. Prompt 20 final audit (2026-10-06)

Prompt 20 is complete: `reports/PROMPT20_FINAL_AUDIT.md` links to the full
audit. It reconfirmed the cached official baseline through Prompt 12/13/14/15
evidence: 625/625 baseline controls, 50/50 analysis artifacts, 25/25 error
analysis artifacts, 29/29 full-report artifacts and current safe suite 322
passed. It found no benchmark-integrity or data-leakage defect.

The audit distinguishes research readiness from service readiness. The current
Docker endpoint is healthy and reports 10/10 configured converters, but a full
GPU/cloud runtime smoke-test was not completed: a sequential one-page probe was
stopped after a prolonged ML/GPU wait. Before claiming all web converters are
operational, add package-level availability probes (the registry currently
tests only a venv executable) and record a bounded smoke-test for each adapter.
Authentication/ownership/rate-limiting, scalable job storage and production
secrets/complete dependency locks remain requirements before public deployment.

Follow-up: Docker images and Compose explicitly set
`PDF_BENCHMARK_WEB_PROJECT_ROOT=/opt/pdf-benchmark`. This fixes converter
discovery after the non-editable package installation: adapter venvs are in the
container project root, not beside `site-packages`. Without it all converters
appeared unavailable and the UI selector was disabled.
