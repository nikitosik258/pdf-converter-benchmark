from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from pdf_benchmark.models import StandardizedDocument

from .captions import normalize_caption
from .chemistry import normalize_chemical_formula
from .latex import normalize_latex
from .tables import normalize_table
from .text import normalize_text


NORMALIZATION_VERSION = "1.0"


class NormalizationConfig(BaseModel):
    version: str = NORMALIZATION_VERSION
    unicode_form: str = "NFC"
    preserve_paragraphs: bool = True
    normalize_quote_glyphs: bool = True

    # Visible line-end '-' deletion is intentionally opt-in and lexicon-gated.
    visible_hyphen_join_lexicon: list[str] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "NormalizationConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        root = payload.get("normalization", payload)
        return cls.model_validate(root)

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _normalize_caption_obj(caption, config: NormalizationConfig) -> None:
    if caption is None:
        return
    caption.normalized_text = normalize_caption(
        caption.text,
        visible_hyphen_join_lexicon=config.visible_hyphen_join_lexicon,
    )


def normalize_document(
    document: StandardizedDocument,
    config: NormalizationConfig | None = None,
) -> StandardizedDocument:
    """Deep-copy and normalize a StandardizedDocument.

    Raw vendor output and raw fields remain untouched. Only dedicated
    normalized_* fields and derived diagram text lists are populated.
    """
    cfg = config or NormalizationConfig()
    normalized = document.model_copy(deep=True)

    for page in normalized.pages:
        for block in page.text_blocks:
            block.normalized_text = normalize_text(
                block.raw_text,
                preserve_paragraphs=cfg.preserve_paragraphs,
                normalize_quote_glyphs=cfg.normalize_quote_glyphs,
                visible_hyphen_join_lexicon=cfg.visible_hyphen_join_lexicon,
            )

        page.tables = [normalize_table(table) for table in page.tables]

        for formula in page.formulas:
            if formula.latex:
                formula.normalized_latex = normalize_latex(formula.latex)
            elif formula.raw_text:
                # Do not hallucinate LaTeX from plain OCR text.
                formula.normalized_latex = None

        for chemical in page.chemical_objects:
            if chemical.subtype == "linear_formula" and chemical.raw_formula:
                chemical.normalized_formula = normalize_chemical_formula(
                    chemical.raw_formula
                )
            # Structure labels are textual OCR, not a molecular graph.
            chemical.text_labels = [
                normalize_text(v, preserve_paragraphs=False)
                for v in chemical.text_labels
            ]

        for image in page.images:
            _normalize_caption_obj(image.caption, cfg)

        for diagram in page.diagrams:
            _normalize_caption_obj(diagram.caption, cfg)
            diagram.text_elements = [
                normalize_text(
                    v,
                    preserve_paragraphs=False,
                    visible_hyphen_join_lexicon=cfg.visible_hyphen_join_lexicon,
                )
                for v in diagram.text_elements
            ]
            diagram.key_elements = [
                normalize_text(
                    v,
                    preserve_paragraphs=False,
                    visible_hyphen_join_lexicon=cfg.visible_hyphen_join_lexicon,
                )
                for v in diagram.key_elements
            ]

    normalized.metadata = dict(normalized.metadata)
    normalized.metadata["normalization"] = {
        "version": cfg.version,
        "config_sha256": cfg.fingerprint(),
        "policy": "conservative_non_corrective",
    }
    return normalized


def normalize_document_json(
    input_path: str | Path,
    output_path: str | Path,
    config_path: str | Path | None = None,
) -> Path:
    cfg = (
        NormalizationConfig.from_yaml(config_path)
        if config_path
        else NormalizationConfig()
    )
    document = StandardizedDocument.model_validate_json(
        Path(input_path).read_text(encoding="utf-8")
    )
    normalized = normalize_document(document, cfg)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        normalized.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return target
