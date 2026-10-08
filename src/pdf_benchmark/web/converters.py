from __future__ import annotations

import os
from dataclasses import dataclass

from pdf_benchmark.benchmark.registry import TOOL_SPECS, environment_python

from .config import WebSettings
from .schemas import ConverterInfo


ACTIVE_CONVERTERS = (
    "pymupdf",
    "pdfplumber",
    "pdfminer",
    "docling",
    "mineru",
    "ocr_space",
    "nutrient",
    "mindee",
    "adobe_extract",
    "llamaparse",
)


@dataclass(frozen=True)
class ConverterDescriptor:
    name: str
    technology: str
    capabilities: tuple[str, ...]
    required_env: tuple[str, ...] = ()


_ALL = ("text", "tables", "math", "chemistry", "images", "diagrams")

DESCRIPTORS: dict[str, ConverterDescriptor] = {
    "pymupdf": ConverterDescriptor("PyMuPDF", "classic", _ALL),
    "pdfplumber": ConverterDescriptor(
        "pdfplumber", "classic", ("text", "tables", "math", "chemistry")
    ),
    "pdfminer": ConverterDescriptor(
        "pdfminer.six", "classic", ("text", "math", "chemistry", "images", "diagrams")
    ),
    "docling": ConverterDescriptor("Docling", "ml", _ALL),
    "mineru": ConverterDescriptor("MinerU", "ml", _ALL),
    "ocr_space": ConverterDescriptor(
        "OCR.Space",
        "cloud",
        ("text", "tables", "math", "chemistry"),
        ("OCR_SPACE_API_KEY",),
    ),
    "nutrient": ConverterDescriptor(
        "Nutrient", "cloud", _ALL, ("NUTRIENT_DWS_EXTRACTION_API_KEY",)
    ),
    "mindee": ConverterDescriptor(
        "Mindee",
        "cloud",
        ("text", "math", "chemistry"),
        ("MINDEE_V2_API_KEY", "MINDEE_OCR_MODEL_ID"),
    ),
    "adobe_extract": ConverterDescriptor(
        "Adobe Extract",
        "cloud",
        ("text", "tables", "chemistry", "images"),
        ("PDF_SERVICES_CLIENT_ID", "PDF_SERVICES_CLIENT_SECRET"),
    ),
    "llamaparse": ConverterDescriptor(
        "LlamaParse", "cloud", _ALL, ("LLAMA_CLOUD_API_KEY",)
    ),
}


class ConverterRegistry:
    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings

    def _info(self, converter_id: str) -> ConverterInfo:
        spec = TOOL_SPECS[converter_id]
        descriptor = DESCRIPTORS[converter_id]
        credentials = None
        reason = None

        try:
            environment_python(self.settings.project_root, spec.environment)
            environment_ready = True
        except FileNotFoundError:
            environment_ready = False
            reason = f"Runtime environment {spec.environment} is not installed"

        if spec.kind == "cloud":
            credentials = all(bool(os.getenv(name)) for name in descriptor.required_env)
            if not self.settings.allow_cloud:
                reason = "Cloud conversion is disabled by server configuration"
            elif not credentials:
                reason = "Server-side provider credentials are not configured"

        return ConverterInfo(
            id=converter_id,
            name=descriptor.name,
            deployment=spec.kind,
            technology=descriptor.technology,  # type: ignore[arg-type]
            available=environment_ready and reason is None,
            unavailable_reason=reason,
            credentials_configured=credentials,
            capabilities=list(descriptor.capabilities),
        )

    def list(self) -> list[ConverterInfo]:
        return [self._info(name) for name in ACTIVE_CONVERTERS]

    def get(self, converter_id: str) -> ConverterInfo | None:
        if converter_id not in ACTIVE_CONVERTERS:
            return None
        return self._info(converter_id)

    def required_env(self, converter_id: str) -> tuple[str, ...]:
        return DESCRIPTORS[converter_id].required_env
