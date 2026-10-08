# AGENTS.md

## Repository operating rules

This repository is a benchmark/research codebase with expensive cached outputs and some paid/external API integrations.

Follow these rules for all work in this repository.

### 1. Preserve benchmark reproducibility

Do not modify without explicit approval:

```text
documents/
ground_truth/
benchmark/object_manifest.json
canonical document IDs D01-D05
GT object IDs / references / bboxes
benchmark category definitions
```

If a requested change would alter benchmark semantics, explain the impact first.

### 2. Never trigger paid/cloud acquisition casually

Do not use:

```text
--force-api
```

unless the user explicitly requests a fresh vendor/API call.

Prefer:

- compatible cached standardized output
- cached raw vendor response + re-standardization
- local downstream recomputation

Do not ask the user to paste API secrets.

### 3. Do not rerun expensive local parsers unnecessarily

Especially avoid rerunning:

```text
Docling
MinerU
```

unless needed for a real parser-level change.

If only matching/evaluation/aggregation changed, reuse cached standardized/normalized data where possible.

### 4. Active benchmark roster

Exactly these ten tools are active unless explicitly changed:

```text
pymupdf
pdfplumber
pdfminer
docling
mineru
ocr_space
nutrient
mindee
adobe_extract
llamaparse
```

Marker is inactive.

Azure Document Intelligence is reserve/inactive.

### 5. Marker

Do not reintroduce Marker.

Legacy Marker files/tests may remain for audit.

Known legacy tests:

```text
tests/test_heavy_local_adapters.py
tests/test_marker_standardizer.py
```

may fail at collection because `MarkerAdapter` is no longer exported.

Safe current test command:

```powershell
.venv\Scripts\python.exe -m pytest -q `
  -m "not heavy" `
  --ignore=tests/test_heavy_local_adapters.py `
  --ignore=tests/test_marker_standardizer.py
```

Expected current result:

```text
308 passed
```

### 6. Text matching

Preserve one-to-one Hungarian behavior as the primary mechanism.

For fragmented text extraction, preserve the multi-block text fallback.
Preserve spatial completion of partial Hungarian text matches: anchors stay
reserved, free blocks have a single geometric owner, and reference text or
evaluation scores must not choose between a single block and an aggregate.
Keep whole-region and nonspatial matches unchanged.

Preserve repeated words at different coordinates. Deduplicate text blocks only
when both normalized text and bbox are identical.

Do not lower text spatial thresholds merely to accept cross-column contamination.

### 7. pdfplumber

Preserve word-level text extraction in raw/standardized output.

Do not revert to line-only standardized text.

Reason: `extract_text_lines()` merged columns on D05 page 10 and caused `TXT_009` to fail incorrectly.

### 8. LlamaParse

Preserve:

- list-valued row/column dimension support
- list-of-dicts bbox support
- stale historical failure metadata handling for valid cached standardized results

Do not assume old `adapter_result.json` failure status invalidates a currently valid compatible standardized cache.

### 8.1. OCR.Space

Preserve line-level `TextOverlay.Lines` text blocks with normalized bboxes. The
cached Engine 3 PDF responses use a documented standardizer default of 2 pixels
per PDF point; record the raw pixel bbox and canvas scale in provenance.

When overlay lines are unavailable, preserve GT-independent ParsedText
segmentation: split on blank lines, then bound malformed giant blocks. Do not
revert to one bbox-less whole-page text block.

The isolated verification is in
`outputs/repairs/ocr_space_text_blocks_v1/`: 5/5 raw caches remain compatible
and plan `standardize_only`, with zero cloud calls. The repair is included in
the current official experiment, where OCR.Space Text Score is 59.26. The
current Prompt 13 report includes this experiment.

### 9. Chemistry

Chemistry is a required category.

Do not silently reinterpret math/image/diagram outputs as `ChemicalObject`.

Do not reinterpret generic math/image/diagram output as chemistry. The shared
GT-independent enrichment may use only strict compound-formula syntax, explicit
structural-formula table semantics, native images under such a header, or
spatial atom/group labels under the same explicit header. Preserve provider
formula text without chemical correction. OCR.Space native ParsedText and
CodeCogs `\\chem{...}` evidence remains valid and authoritative.

The current official experiment applies this common detector to all ten tools.
Every tool detects 6/6 Chemistry GT objects and has Detection 100.0, while
conditional Structured Extraction ranges from 39.04 to 75.13. The isolated
raw-cache audit is under `outputs/repairs/shared_chemistry_detection_v1/`;
OCR.Space-specific historical evidence remains under
`outputs/repairs/ocr_space_chemistry_objects_v1/`.

Publish chemistry coverage and payload quality separately:

- `chemistry_detection_score`: all Chemistry GT objects, document macro then
  equal-weight subtype macro;
- `chemistry_structured_extraction_score`: matched Chemistry objects only,
  document macro then detected-subtype macro;
- when nothing is detected, structured extraction is N/A and its aggregate
  record is omitted.

These are diagnostic reporting scores. They do not replace or change the
existing Chemistry Score or Overall.

### 10. VRAM

Published `peak_vram_mb` must represent attributable GPU use as faithfully as possible.

Current intended logic:

```text
process GPU peak, if available
else device-wide GPU used only when adapter Torch telemetry proves CUDA activity
else 0
```

Do not publish `torch_peak_reserved_mb` as physical VRAM.

Do not assign unrelated device-wide GPU usage to CPU/cloud tools.

### 11. Outliers

Do not remove outliers automatically.

Current final benchmark contains 13 statistical warnings, mainly Docling/MinerU resource and Diagram Score outliers, low Text/Table Score outliers, and Chemistry/Text observations.

Treat statistical outliers as observations unless proven corrupt.

### 12. Final benchmark invariant

Latest clean experiment:

```text
20261004T095716Z_s42_2334c932
```

This is the cached raw re-standardization after all confirmed Prompt 12 repairs
and the shared GT-independent chemistry and math enrichment. All 50 pairs used
`standardize_only`; adapter acquisitions and cloud API calls were zero. The
previous official experiment `20261004T013334Z_s42_20904978`, its inputs and
2,284 legacy cache files remain unchanged. `outputs/benchmark/latest_experiment.txt`
selects the current baseline.

Expected structure:

```text
10 tool rows
50 pair rows
400 object rows
0 errors
13 warnings
status=clean
```

Control Prompt 12 passed 625/625 checks. Entry point:
`reports/PROMPT12_CONTROL.md`; machine-readable evidence is under
`reports/benchmark_control/20261004T095716Z_s42_2334c932/`. Prompt 13 is complete
for this baseline. Its entry point is `reports/PROMPT13_ANALYSIS.md`; artifacts
are under `reports/statistical_analysis/20261004T095716Z_s42_2334c932/` (24 CSV tables, 17
figures in PNG/SVG, HTML/Markdown reports and a 17-page PDF atlas). Preparation
passed 601/601 checks, artifact verification passed 50/50 checks, and the
current safe suite passes 322 tests. Prompt 14 error analysis is complete for
the same baseline. Its entry point is `reports/PROMPT14_ERROR_ANALYSIS.md`;
artifacts are under `reports/error_analysis/20261004T095716Z_s42_2334c932/`. It
contains 60 deterministic examples (six categories for every tool), 21 source
crops and 14 prediction assets. Artifact verification passed 25/25 checks.
Prompt 15 is complete for the same baseline. Its entry point is
`reports/PROMPT15_FULL_REPORT.md`; artifacts are under
`reports/final_report/20261004T095716Z_s42_2334c932/`. The report has all 20
requested sections, 17 figures, metric formulas, fixed tool versions and
official documentation links. It includes exactly five verified examples per
tool (50 total), selected from Prompt 14 without defining a new aggregate
score. Artifact verification passed 29/29 checks. Prompt 16 architecture is at
`reports/PROMPT16_WEB_ARCHITECTURE.md`. Prompt 17 implements a single-node
FastAPI backend under `src/pdf_benchmark/web/`; its entry point is
`reports/PROMPT17_FASTAPI_BACKEND.md`. Prompt 18 adds the static browser
interface at `reports/PROMPT18_WEB_INTERFACE.md`. The current safe suite passes
322 tests.
Prompt 19 deployment material is at `reports/PROMPT19_DOCKER_DEPLOYMENT.md`:
CPU/GPU Dockerfiles, Compose, Caddy HTTPS configuration and a server environment
template. Static Compose validation passed; no Docker image, heavy parser or
cloud adapter was run. The current safe suite passes 322 tests.
Prompt 20 final audit is at `reports/PROMPT20_FINAL_AUDIT.md`. It confirms
research/report readiness while recording web production blockers: package-level
converter availability probes, bounded all-adapter GPU/cloud smoke tests,
authentication/ownership/rate limits, scalable job storage, complete locks and
production secrets. Do not claim public deployment readiness until these are
resolved.
No benchmark inputs, caches, scores, heavy parsers or cloud APIs were changed
or invoked by the web implementation.

Audit update (2026-10-02): `reports/PROMPT12_CODE_AUDIT.md` documents confirmed
adapter, matching, normalization and GT defects affecting these scores.
`clean` remains a structural integrity status, not methodological validation.
Prompt 13 artifacts for earlier experiments are historical. Audit probes are
not official benchmark results. Do not silently change GT, the current baseline
or cached outputs when addressing the findings.

Repair progress: Nutrient coordinate canvas (A01), MinerU string tables (A02)
and Adobe indexed table paths (A03) are fixed in the adapters. Repaired copies
are in `outputs/repairs/nutrient_coordinates_v1/`,
`outputs/repairs/mineru_tables_v1/` and `outputs/repairs/adobe_table_paths_v1/`;
Docling picture classification (A04) and MinerU visual assets/captions (part of
A05) are also fixed. Their copies are in
`outputs/repairs/docling_picture_classification_v1/` and
`outputs/repairs/mineru_visual_assets_v1/`. The official experiment and shared
cache are unchanged. Docling picture child text (remaining A05) and spatial
completion of partial text matches (A06) are also fixed; verification copies
are in `outputs/repairs/docling_picture_text_v1/` and
`outputs/repairs/text_region_alignment_v1/`.
Mindee word-level text (A07) and pdfminer parent/child text hierarchy (part of
A08) are fixed as well. Their isolated copies are in
`outputs/repairs/mindee_words_v1/` and
`outputs/repairs/pdfminer_text_hierarchy_v1/`.
PyMuPDF native line/span preservation (remaining A08) and LaTeX control-word
boundaries (first part of A09) are fixed. Their isolated verification artifacts
are in `outputs/repairs/pymupdf_text_lines_v1/` and
`outputs/repairs/latex_command_boundaries_v1/`. Raw/normalized math payload
separation (remaining A09) and stage-aware cache fingerprinting/invalidation
(A10) are fixed. Their combined offline verification is in
`outputs/repairs/math_payload_cache_fingerprints_v1/`. New benchmark stages use
versioned subdirectories and can re-standardize compatible saved raw without
overwriting the legacy cache or invoking an adapter acquisition. LaTeX spacing
normalization is now idempotent across all 1,507 cached formula objects, and the
explicitly authorized GT correction for the left operator index in MATH_002–004
is applied in both GT sources. Its exact reversible diff and isolated score
check are in `outputs/repairs/latex_idempotence_gt_indices_v1/`; IDs, bboxes and
the object manifest are unchanged. The explicitly authorized MATH_005/MATH_006
unit-alignment repair is also complete: MATH_005 now represents only numbered
equation (6), while MATH_006 represents the complete seven-line equation (57).
The exact reversible two-object diff and cached diagnostic comparison are in
`outputs/repairs/math_gt_units_v1/`; IDs, pages, categories and the object
manifest are unchanged. The CHEM_001 native-word bbox and sampled visual-GT
detection protocol are fixed and verified offline in
`outputs/repairs/chem_bbox_visual_detection_v1/`. The visual protocol no longer
labels unmatched candidates as false positives when page annotation is not
exhaustive; `exhaustive_page_f1_v1` remains available for fully annotated
pages. Visual assignments are unchanged. During this repair CHEM_002 was
confirmed to have a separately clipped bbox; its explicitly authorized native
word bbox repair is now complete and verified in
`outputs/repairs/chem002_bbox_v1/`. No confirmed repair items remain in
`CODEX_CONTEXT.md`, section 25. The cached official recomputation and control
Prompt 12 are complete. Post-Prompt-13 OCR.Space line-level text, strict
compound-formula extraction and explicit CodeCogs chemistry structures are
also fixed. Their isolated artifacts are in
`outputs/repairs/ocr_space_text_blocks_v1/` and
`outputs/repairs/ocr_space_chemistry_objects_v1/`. Chemistry detection and
conditional structured-extraction reporting are now separated without changing
the existing Chemistry Score or Overall. The later shared detector routes all
ten tools through the same explicit-evidence policy; its raw audit, cache plan,
object inventory and score comparison are in
`outputs/repairs/shared_chemistry_detection_v1/`. No proposed post-Prompt-13
chemistry repair items remain. The shared GT-independent math enrichment is
also included in the current official experiment; its isolated audit is in
`outputs/repairs/math_extraction_v1/`. It adds formulas only from explicit
provider LaTeX or numbered-equation layout evidence, preserves native formulas,
and does not reconstruct LaTeX for plain layout text. The official cached
recomputation changed 31 Math object scores, all upward, and left the other 369
object scores unchanged.

When `reuse_standardized` copies a stage into a new versioned cache, preserve
its local `assets/` tree together with `standardized.json`. Cache transport code
is part of the standardization fingerprint. The incomplete intermediate
experiment `20261003T202119Z_s42_eca66135` is `needs_attention` and is
superseded by the current baseline.

### 13. Testing discipline

For code changes:

1. explain intended change briefly
2. modify the smallest necessary surface
3. run focused tests
4. run safe non-heavy suite
5. do not run heavy/cloud integrations unless required
6. summarize files changed and semantic impact

### 14. Windows commands

The user works primarily in PowerShell on Windows.

Prefer commands that are directly copy-pasteable in PowerShell.

### 15. User interaction

Keep instructions concise and technical.

When manual local work is needed, end with:

```text
### Что нужно сделать вручную
...
```

If none:

```text
### Что нужно сделать вручную
Ничего.
```

### 16. Research integrity

Never change benchmark inputs or evaluation rules merely to improve a tool's score.

Any benchmark-affecting change must have a documented methodological reason.
