# Ground Truth input expected by Prompt 11

The runner reads either:

- `ground_truth/objects.jsonl` (preferred), or
- `ground_truth/objects.json`.

There must be exactly the 40 object IDs from `benchmark/object_manifest.json`
for a full run.

Minimal object envelope:

```json
{
  "object_id": "TXT_001",
  "document_id": "D01",
  "page": 15,
  "object_type": "text",
  "bbox": [0.10, 0.20, 0.80, 0.35],
  "reference": {
    "raw_text": "...",
    "normalized_text": "..."
  }
}
```

`reference` may also use the Prompt-4 direct fields. The loader accepts:
- text: `raw_text`, `normalized_text`, `text`
- math: `latex`, `normalized_latex`
- chemistry: `raw_formula`, `normalized_formula`, `formula`
- tables: Table-compatible `rows`, `columns`, `cells`, `caption`
- image/diagram/structure: `bbox`, `caption`, `text_labels`,
  `text_elements`, `key_elements`, `subfigures`.
