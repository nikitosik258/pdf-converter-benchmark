from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


CategoryName = Literal[
    "text",
    "table",
    "math",
    "chemistry",
    "image",
    "diagram",
]

ObjectType = Literal[
    "text",
    "table",
    "math_formula",
    "chemical_formula",
    "chemical_structure",
    "image",
    "diagram",
]

ScopeName = Literal[
    "object",
    "page",
    "document",
    "category",
    "tool",
    "benchmark",
]


class MetricRecord(BaseModel):
    """One metric in long/relational form.

    metric_value is ALWAYS the quality-oriented 0..100 value where larger is
    better. raw_value retains the natural metric when useful:
      CER raw_value=0.12, metric_value=88
      Precision raw_value=0.8, metric_value=80
    """

    tool: str
    document_id: str
    page: int | None = Field(default=None, ge=1)
    object_id: str | None = None
    object_type: str | None = None
    category: CategoryName | None = None
    subtype: str | None = None
    scope: ScopeName = "object"

    metric_name: str
    raw_value: float | None = None
    metric_value: float = Field(ge=0.0, le=100.0)
    higher_is_better: bool = True

    details: dict[str, Any] = Field(default_factory=dict)


class ObjectEvaluationResult(BaseModel):
    tool: str
    document_id: str
    page: int = Field(ge=1)
    object_id: str
    object_type: ObjectType
    category: CategoryName
    subtype: str | None = None

    matched: bool = True
    metrics: list[MetricRecord] = Field(default_factory=list)
    object_score: float = Field(ge=0.0, le=100.0)
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metric_identity(self):
        for metric in self.metrics:
            if metric.tool != self.tool:
                raise ValueError("Metric tool does not match ObjectEvaluationResult.tool")
            if metric.document_id != self.document_id:
                raise ValueError("Metric document_id does not match object result")
            if metric.page != self.page:
                raise ValueError("Object metric page does not match object result")
            if metric.object_id != self.object_id:
                raise ValueError("Object metric object_id does not match object result")
        return self


class AggregateScoreRecord(BaseModel):
    tool: str
    scope: Literal["page", "document", "category", "tool", "benchmark"]
    score_name: str
    score: float = Field(ge=0.0, le=100.0)

    document_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    category: CategoryName | None = None

    n_objects: int = Field(default=0, ge=0)
    n_documents: int = Field(default=0, ge=0)
    std_dev: float | None = Field(default=None, ge=0.0)
    ci95_low: float | None = Field(default=None, ge=0.0, le=100.0)
    ci95_high: float | None = Field(default=None, ge=0.0, le=100.0)
    ci_basis: str | None = None

    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ci(self):
        if self.ci95_low is not None and self.ci95_high is not None:
            if self.ci95_low > self.ci95_high:
                raise ValueError("ci95_low must be <= ci95_high")
        return self


class EvaluationObjectInput(BaseModel):
    """Serializable request for evaluating one already-matched benchmark object.

    The matching layer may set prediction=None for a missed object.

    Payload conventions:
    - text: {"text": "...", optional "reading_order": ["b1", "b2"]}
    - table: Table-compatible dict
    - math_formula: Formula-compatible dict OR {"latex": "..."}
    - chemical_formula: {"formula": "..."} or ChemicalObject-compatible dict
    - chemical_structure: {"bbox": ..., "text_labels": [...], "extraction_success": bool}
    - image: ImageObject-compatible dict
    - diagram: DiagramObject-compatible dict

    context can carry page-level matching information such as:
      {"detection_tp": 2, "detection_fp": 1, "detection_fn": 0}
    """

    tool: str
    document_id: str
    page: int = Field(ge=1)
    object_id: str
    object_type: ObjectType
    reference: dict[str, Any]
    prediction: dict[str, Any] | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    schema_version: str = "1.0"
    object_results: list[ObjectEvaluationResult] = Field(default_factory=list)
    metric_records: list[MetricRecord] = Field(default_factory=list)
    aggregate_scores: list[AggregateScoreRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
