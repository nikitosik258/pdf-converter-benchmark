from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class BBox(BaseModel):
    """Normalized top-left bounding box in [0, 1]."""

    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)

    @property
    def as_list(self) -> list[float]:
        return [self.x_min, self.y_min, self.x_max, self.y_max]


class Caption(BaseModel):
    text: str = ""
    normalized_text: str | None = None
    bbox: BBox | None = None


class TextBlock(BaseModel):
    element_id: str
    page_number: int = Field(ge=1)
    bbox: BBox | None = None
    raw_text: str = ""
    normalized_text: str | None = None
    block_type: Literal[
        "paragraph",
        "heading",
        "list_item",
        "caption",
        "header",
        "footer",
        "unknown",
    ] = "unknown"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order_index: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class Formula(BaseModel):
    element_id: str
    page_number: int = Field(ge=1)
    bbox: BBox | None = None
    raw_text: str | None = None
    latex: str | None = None
    normalized_latex: str | None = None
    plain_text: str | None = None
    formula_type: Literal["inline", "display", "unknown"] = "unknown"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order_index: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class ChemicalObject(BaseModel):
    element_id: str
    page_number: int = Field(ge=1)
    bbox: BBox | None = None
    subtype: Literal["linear_formula", "structure"]
    raw_formula: str | None = None
    normalized_formula: str | None = None
    text_labels: list[str] = Field(default_factory=list)
    asset_path: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order_index: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class TableCell(BaseModel):
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    text: str = ""
    normalized_text: str | None = None
    is_header: bool = False
    bbox: BBox | None = None
    embedded_objects: list[str] = Field(default_factory=list)


class Table(BaseModel):
    element_id: str
    page_number: int = Field(ge=1)
    bbox: BBox | None = None
    rows: int = Field(default=0, ge=0)
    columns: int = Field(default=0, ge=0)
    cells: list[TableCell] = Field(default_factory=list)
    caption: Caption | None = None
    html: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order_index: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class Subfigure(BaseModel):
    label: str | None = None
    bbox: BBox | None = None


class ImageObject(BaseModel):
    element_id: str
    page_number: int = Field(ge=1)
    bbox: BBox | None = None
    asset_path: str | None = None
    caption: Caption | None = None
    subfigures: list[Subfigure] = Field(default_factory=list)
    extraction_success: bool = False
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order_index: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class DiagramObject(BaseModel):
    element_id: str
    page_number: int = Field(ge=1)
    bbox: BBox | None = None
    diagram_type: Literal[
        "chart",
        "electrical_scheme",
        "neural_network",
        "flowchart",
        "scientific_scheme",
        "unknown",
    ] = "unknown"
    asset_path: str | None = None
    caption: Caption | None = None
    text_elements: list[str] = Field(default_factory=list)
    key_elements: list[str] = Field(default_factory=list)
    subfigures: list[Subfigure] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order_index: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class Page(BaseModel):
    page_number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    text_blocks: list[TextBlock] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    formulas: list[Formula] = Field(default_factory=list)
    chemical_objects: list[ChemicalObject] = Field(default_factory=list)
    images: list[ImageObject] = Field(default_factory=list)
    diagrams: list[DiagramObject] = Field(default_factory=list)
    reading_order: list[str] = Field(default_factory=list)


class ToolMetadata(BaseModel):
    tool_name: str
    distribution_name: str
    version: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    model_versions: dict[str, str] = Field(default_factory=dict)


class StandardizedDocument(BaseModel):
    schema_version: str = "1.0"
    document_id: str
    source_pdf: str
    tool: ToolMetadata
    pages: list[Page] = Field(default_factory=list)
    raw_artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceUsage(BaseModel):
    wall_time_seconds: float = 0.0
    cpu_time_seconds: float = 0.0
    average_cpu_percent_of_machine: float | None = None
    peak_rss_mb: float | None = None
    peak_process_tree_rss_mb: float | None = None
    gpu_peak_process_mb: float | None = None
    gpu_peak_device_used_mb: float | None = None
    torch_peak_allocated_mb: float | None = None
    torch_peak_reserved_mb: float | None = None


class RawToolResult(BaseModel):
    primary_artifact: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AdapterRunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    document_id: str
    tool_name: str
    status: RunStatus
    started_at: str
    finished_at: str
    resource_usage: ResourceUsage
    raw_result: RawToolResult | None = None
    standardized_path: str | None = None
    run_metadata_path: str | None = None
    document: StandardizedDocument | None = None
    warnings: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None

    @staticmethod
    def utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
