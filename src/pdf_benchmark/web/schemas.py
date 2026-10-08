from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pdf_benchmark.models import StandardizedDocument


class JobStatus(str, Enum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PROCESSING = "processing"
    STANDARDIZING = "standardizing"
    EXPORTING = "exporting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.EXPIRED,
}


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    original_filename: str
    content_type: str | None = None
    size_bytes: int = Field(ge=1)
    sha256: str
    page_count: int = Field(ge=1)
    created_at: datetime
    expires_at: datetime


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    converter_id: str
    status: JobStatus
    stage: str
    created_at: datetime
    expires_at: datetime
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    processing_time_seconds: float | None = Field(default=None, ge=0)
    attempt: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    error_retryable: bool = False
    result_json_path: str | None = None
    text_path: str | None = None
    adapter_output_path: str | None = None


class ConverterInfo(BaseModel):
    id: str
    name: str
    deployment: Literal["local", "cloud"]
    technology: Literal["classic", "ml", "cloud"]
    available: bool
    unavailable_reason: str | None = None
    credentials_configured: bool | None = None
    capabilities: list[str]


class DocumentResponse(BaseModel):
    id: str
    filename: str
    size_bytes: int
    sha256: str
    page_count: int
    created_at: datetime
    expires_at: datetime
    content_url: str


class ConversionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=36, max_length=36)
    converter_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")


class JobResponse(BaseModel):
    id: str
    document_id: str
    converter_id: str
    status: JobStatus
    stage: str
    created_at: datetime
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    processing_time_seconds: float | None
    warnings: list[str]
    error: "ErrorDetail | None" = None
    result_url: str | None = None
    text_download_url: str | None = None
    json_download_url: str | None = None


class ResultResponse(BaseModel):
    job_id: str
    converter_id: str
    processing_time_seconds: float
    document: StandardizedDocument


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    storage: Literal["ok", "error"]
    executor: Literal["ok", "stopped"]
    converters_available: int = Field(ge=0)
    converters_total: int = Field(ge=0)


JobResponse.model_rebuild()
