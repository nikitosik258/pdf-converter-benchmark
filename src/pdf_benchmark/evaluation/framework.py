from __future__ import annotations

from typing import Any

from pdf_benchmark.models import (
    BBox,
    ChemicalObject,
    DiagramObject,
    Formula,
    ImageObject,
    Table,
)

from .aggregation import (
    aggregate_benchmark,
    aggregate_document_scores,
    aggregate_page_scores,
    aggregate_tool_scores,
)
from .chemistry_metrics import (
    calculate_linear_chemistry_metrics,
    calculate_structure_chemistry_metrics,
)
from .diagram_metrics import calculate_diagram_metrics
from .image_metrics import calculate_image_metrics, detection_metrics
from .math_metrics import calculate_math_metrics
from .models import (
    EvaluationObjectInput,
    EvaluationReport,
    MetricRecord,
    ObjectEvaluationResult,
)
from .scoring import ScoringConfig
from .table_metrics import calculate_table_metrics
from .text_metrics import calculate_text_metrics


_SCORE_METRIC_BY_TYPE = {
    "text": "text_score",
    "table": "table_score",
    "math_formula": "math_score",
    "chemical_formula": "chem_linear_score",
    "chemical_structure": "chem_structure_score",
    "image": "image_score",
    "diagram": "diagram_score",
}

_CATEGORY_BY_TYPE = {
    "text": "text",
    "table": "table",
    "math_formula": "math",
    "chemical_formula": "chemistry",
    "chemical_structure": "chemistry",
    "image": "image",
    "diagram": "diagram",
}

_SUBTYPE_BY_TYPE = {
    "chemical_formula": "linear_formula",
    "chemical_structure": "structure",
}


class EvaluationFramework:
    def __init__(self, scoring_config: ScoringConfig | None = None):
        self.scoring_config = scoring_config or ScoringConfig()

    @staticmethod
    def _bbox(value: Any) -> BBox | None:
        if value is None:
            return None
        if isinstance(value, BBox):
            return value
        if isinstance(value, (list, tuple)) and len(value) == 4:
            return BBox(
                x_min=value[0],
                y_min=value[1],
                x_max=value[2],
                y_max=value[3],
            )
        return BBox.model_validate(value)

    @staticmethod
    def _caption_text(payload: dict[str, Any]) -> str | None:
        caption = payload.get("caption")
        if caption is None:
            return None
        if isinstance(caption, str):
            return caption
        if isinstance(caption, dict):
            return caption.get("text")
        return getattr(caption, "text", None)

    @staticmethod
    def _metric_record(
        req: EvaluationObjectInput,
        category: str,
        subtype: str | None,
        name: str,
        raw: float,
        *,
        higher_is_better: bool = True,
        quality_value: float | None = None,
    ) -> MetricRecord:
        q = raw if quality_value is None else quality_value
        q = max(0.0, min(1.0, float(q)))
        return MetricRecord(
            tool=req.tool,
            document_id=req.document_id,
            page=req.page,
            object_id=req.object_id,
            object_type=req.object_type,
            category=category,
            subtype=subtype,
            scope="object",
            metric_name=name,
            raw_value=float(raw),
            metric_value=100.0 * q,
            higher_is_better=higher_is_better,
        )

    def _records(
        self,
        req: EvaluationObjectInput,
        category: str,
        subtype: str | None,
        metrics: dict[str, float | None],
    ) -> list[MetricRecord]:
        records: list[MetricRecord] = []
        for name, raw in metrics.items():
            if raw is None or name.endswith("_score"):
                continue
            if name in {"cer", "wer"}:
                score_key = f"{name}_score"
                quality = metrics.get(score_key)
                if quality is None:
                    continue
                records.append(
                    self._metric_record(
                        req,
                        category,
                        subtype,
                        name,
                        raw,
                        higher_is_better=False,
                        quality_value=float(quality),
                    )
                )
            elif name in {"cer_score", "wer_score"}:
                # represented by CER/WER rows above; avoid duplicate score rows
                continue
            else:
                records.append(
                    self._metric_record(
                        req,
                        category,
                        subtype,
                        name,
                        raw,
                    )
                )
        return records

    def evaluate_object(
        self,
        request: EvaluationObjectInput | dict[str, Any],
    ) -> ObjectEvaluationResult:
        req = (
            request
            if isinstance(request, EvaluationObjectInput)
            else EvaluationObjectInput.model_validate(request)
        )
        category = _CATEGORY_BY_TYPE[req.object_type]
        subtype = _SUBTYPE_BY_TYPE.get(req.object_type)
        pred = req.prediction
        matched = pred is not None

        if req.object_type == "text":
            ref_text = str(req.reference.get("text", req.reference.get("raw_text", "")))
            pred_text = (
                str(pred.get("text", pred.get("raw_text", "")))
                if pred is not None
                else ""
            )
            ref_order = req.reference.get("reading_order")
            pred_order = pred.get("reading_order") if pred else None
            metrics = calculate_text_metrics(
                ref_text,
                pred_text,
                reference_order=ref_order,
                prediction_order=pred_order,
            )

        elif req.object_type == "table":
            ref_table = Table.model_validate(req.reference)
            pred_table = Table.model_validate(pred) if pred is not None else None
            metrics = calculate_table_metrics(ref_table, pred_table)

        elif req.object_type == "math_formula":
            ref_latex = str(
                req.reference.get(
                    "latex",
                    req.reference.get("raw_text", ""),
                )
                or ""
            )
            pred_latex = (
                str(pred.get("latex", pred.get("raw_text", "")) or "")
                if pred is not None
                else ""
            )
            ref_normalized = req.reference.get("normalized_latex")
            pred_normalized = pred.get("normalized_latex") if pred else None
            metrics = calculate_math_metrics(
                ref_latex,
                pred_latex,
                reference_normalized_latex=(
                    str(ref_normalized) if ref_normalized is not None else None
                ),
                prediction_normalized_latex=(
                    str(pred_normalized) if pred_normalized is not None else None
                ),
            )

        elif req.object_type == "chemical_formula":
            ref_formula = str(
                req.reference.get(
                    "formula",
                    req.reference.get(
                        "raw_formula",
                        req.reference.get("normalized_formula", ""),
                    ),
                )
                or ""
            )
            pred_formula = (
                str(
                    pred.get(
                        "formula",
                        pred.get(
                            "raw_formula",
                            pred.get("normalized_formula", ""),
                        ),
                    )
                    or ""
                )
                if pred is not None
                else ""
            )
            metrics = calculate_linear_chemistry_metrics(
                ref_formula,
                pred_formula,
            )
            metrics["detection"] = 1.0 if matched else 0.0
            metrics["structured_extraction"] = (
                metrics["chem_linear_score"] if matched else None
            )

        elif req.object_type == "chemical_structure":
            metrics = calculate_structure_chemistry_metrics(
                reference_bbox=self._bbox(req.reference.get("bbox")),
                prediction_bbox=self._bbox(pred.get("bbox")) if pred else None,
                reference_labels=list(req.reference.get("text_labels") or []),
                prediction_labels=list(pred.get("text_labels") or []) if pred else [],
                prediction_exists=matched,
                extraction_success=bool(
                    (pred or {}).get(
                        "extraction_success",
                        bool((pred or {}).get("asset_path")),
                    )
                ),
            )

        elif req.object_type == "image":
            detection_override = None
            detection_metric_name = "detection_recall"
            detection_policy = req.context.get("detection_policy")
            if detection_policy == "exhaustive_page_f1_v1" and {
                "detection_tp",
                "detection_fp",
                "detection_fn",
            }.issubset(req.context):
                detection_override = detection_metrics(
                    int(req.context["detection_tp"]),
                    int(req.context["detection_fp"]),
                    int(req.context["detection_fn"]),
                )["detection_f1"]
                detection_metric_name = "detection_f1"
            elif detection_policy is None and {
                "detection_tp",
                "detection_fp",
                "detection_fn",
            }.issubset(req.context):
                # Historical match files predate an explicit policy and used
                # exhaustive page-level F1.
                detection_override = detection_metrics(
                    int(req.context["detection_tp"]),
                    int(req.context["detection_fp"]),
                    int(req.context["detection_fn"]),
                )["detection_f1"]
                detection_metric_name = "detection_f1"

            metrics = calculate_image_metrics(
                reference_bbox=self._bbox(req.reference.get("bbox")),
                prediction_bbox=self._bbox(pred.get("bbox")) if pred else None,
                reference_caption=self._caption_text(req.reference),
                prediction_caption=self._caption_text(pred or {}),
                prediction_exists=matched,
                extraction_success=bool(
                    (pred or {}).get("extraction_success", False)
                ),
                detection_score_override=detection_override,
                detection_metric_name=detection_metric_name,
            )

        elif req.object_type == "diagram":
            detection_override = None
            detection_metric_name = "detection_recall"
            detection_policy = req.context.get("detection_policy")
            if detection_policy == "exhaustive_page_f1_v1" and {
                "detection_tp",
                "detection_fp",
                "detection_fn",
            }.issubset(req.context):
                detection_override = detection_metrics(
                    int(req.context["detection_tp"]),
                    int(req.context["detection_fp"]),
                    int(req.context["detection_fn"]),
                )["detection_f1"]
                detection_metric_name = "detection_f1"
            elif detection_policy is None and {
                "detection_tp",
                "detection_fp",
                "detection_fn",
            }.issubset(req.context):
                detection_override = detection_metrics(
                    int(req.context["detection_tp"]),
                    int(req.context["detection_fp"]),
                    int(req.context["detection_fn"]),
                )["detection_f1"]
                detection_metric_name = "detection_f1"

            metrics = calculate_diagram_metrics(
                reference_bbox=self._bbox(req.reference.get("bbox")),
                prediction_bbox=self._bbox(pred.get("bbox")) if pred else None,
                reference_caption=self._caption_text(req.reference),
                prediction_caption=self._caption_text(pred or {}),
                reference_text_elements=list(
                    req.reference.get("text_elements") or []
                ),
                prediction_text_elements=list(
                    (pred or {}).get("text_elements") or []
                ),
                reference_key_elements=list(
                    req.reference.get("key_elements") or []
                ),
                prediction_key_elements=list(
                    (pred or {}).get("key_elements") or []
                ),
                reference_subfigure_count=len(
                    req.reference.get("subfigures") or []
                ),
                prediction_subfigure_count=len(
                    (pred or {}).get("subfigures") or []
                ),
                prediction_exists=matched,
                detection_score_override=detection_override,
                detection_metric_name=detection_metric_name,
            )
        else:  # pragma: no cover - protected by Literal validation
            raise ValueError(f"Unsupported object_type: {req.object_type}")

        score_key = _SCORE_METRIC_BY_TYPE[req.object_type]
        # End-to-end benchmark rule: if a required GT object is not produced at
        # all, its object score is exactly zero. Primitive metrics are still
        # retained for diagnosis, but "not supported" can never receive credit
        # from vacuous submetrics (for example merged-cell F1 on an empty table).
        score = 0.0 if pred is None else 100.0 * float(metrics[score_key])
        records = self._records(req, category, subtype, metrics)

        details = {
            "score_metric": score_key,
            "normalized_metric_values": {
                k: v for k, v in metrics.items() if v is not None
            },
        }
        if req.object_type in {"image", "diagram"}:
            detection_policy = req.context.get("detection_policy")
            if detection_policy is None:
                legacy_counts = {
                    "detection_tp",
                    "detection_fp",
                    "detection_fn",
                }.issubset(req.context)
                detection_policy = (
                    "legacy_exhaustive_page_f1"
                    if legacy_counts
                    else "object_presence_recall"
                )
            details["detection_policy"] = detection_policy
        elif req.object_type in {"chemical_formula", "chemical_structure"}:
            details["chemistry_reporting"] = {
                "detection_metric": "detection",
                "structured_extraction_metric": "structured_extraction",
                "structured_extraction_conditional_on_detection": True,
                "combined_score_unchanged": True,
            }

        return ObjectEvaluationResult(
            tool=req.tool,
            document_id=req.document_id,
            page=req.page,
            object_id=req.object_id,
            object_type=req.object_type,
            category=category,
            subtype=subtype,
            matched=matched,
            metrics=records,
            object_score=score,
            details=details,
        )

    def evaluate_page(
        self,
        object_results: list[ObjectEvaluationResult],
    ):
        return aggregate_page_scores(object_results)

    def evaluate_document(
        self,
        object_results: list[ObjectEvaluationResult],
    ):
        return aggregate_document_scores(object_results)

    def evaluate_tool(
        self,
        object_results: list[ObjectEvaluationResult],
    ):
        return aggregate_tool_scores(
            object_results,
            config=self.scoring_config,
        )

    def evaluate_benchmark(
        self,
        object_results: list[ObjectEvaluationResult],
    ) -> EvaluationReport:
        return aggregate_benchmark(
            object_results,
            config=self.scoring_config,
        )
