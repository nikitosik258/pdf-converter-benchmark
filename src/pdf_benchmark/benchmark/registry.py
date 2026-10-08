from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Literal["local", "cloud"]
    environment: str
    config_filename: str
    adapter_module: str = ""
    raw_acquisition_version: str = "1"
    standardization_version: str = "1"


TOOL_SPECS: dict[str, ToolSpec] = {
    "pymupdf": ToolSpec(
        "pymupdf", "local", ".venv", "pymupdf.yaml",
        "src/pdf_benchmark/adapters/local/pymupdf_adapter.py",
        standardization_version="2",
    ),
    "pdfplumber": ToolSpec(
        "pdfplumber", "local", ".venv", "pdfplumber.yaml",
        "src/pdf_benchmark/adapters/local/pdfplumber_adapter.py",
    ),
    "docling": ToolSpec(
        "docling", "local", ".venv", "docling.yaml",
        "src/pdf_benchmark/adapters/local/docling_adapter.py",
        standardization_version="3",
    ),
    "pdfminer": ToolSpec(
        "pdfminer", "local", ".venv", "pdfminer.yaml",
        "src/pdf_benchmark/adapters/local/pdfminer_adapter.py",
        standardization_version="2",
    ),
    "mineru": ToolSpec(
        "mineru", "local", ".venv-mineru", "mineru.yaml",
        "src/pdf_benchmark/adapters/local/mineru_adapter.py",
        standardization_version="3",
    ),
    "ocr_space": ToolSpec(
        "ocr_space", "cloud", ".venv-cloud", "ocr_space.yaml",
        "src/pdf_benchmark/adapters/cloud/ocr_space_adapter.py",
    ),
    "nutrient": ToolSpec(
        "nutrient", "cloud", ".venv-cloud", "nutrient.yaml",
        "src/pdf_benchmark/adapters/cloud/nutrient_adapter.py",
        standardization_version="2",
    ),
    "mindee": ToolSpec(
        "mindee", "cloud", ".venv-cloud", "mindee.yaml",
        "src/pdf_benchmark/adapters/cloud/mindee_adapter.py",
        standardization_version="2",
    ),
    "adobe_extract": ToolSpec(
        "adobe_extract", "cloud", ".venv-cloud", "adobe_extract.yaml",
        "src/pdf_benchmark/adapters/cloud/adobe_extract_adapter.py",
        standardization_version="2",
    ),
    "llamaparse": ToolSpec(
        "llamaparse", "cloud", ".venv-cloud", "llamaparse.yaml",
        "src/pdf_benchmark/adapters/cloud/llamaparse_adapter.py",
    ),
    "azure_document_intelligence": ToolSpec(
        "azure_document_intelligence",
        "cloud",
        ".venv-cloud",
        "azure_document_intelligence.yaml",
        "src/pdf_benchmark/adapters/cloud/azure_document_intelligence_adapter.py",
    ),
}


def environment_python(project_root: Path, environment: str) -> Path:
    env_dir = project_root / environment
    win = env_dir / "Scripts" / "python.exe"
    if win.exists():
        return win
    unix = env_dir / "bin" / "python"
    if unix.exists():
        return unix
    raise FileNotFoundError(
        f"Python executable not found for environment {environment!r}: {env_dir}"
    )


def marker_runtime_env(project_root: Path) -> dict[str, str]:
    """Environment overrides required by Marker/Surya on Windows CPU."""
    path_file = project_root / ".tools" / "llama.cpp" / "llama-server.path"
    if not path_file.exists():
        return {}
    binary = path_file.read_text(encoding="utf-8-sig").strip()
    if not binary:
        return {}
    return {
        "LLAMA_CPP_BINARY": binary,
        "SURYA_INFERENCE_BACKEND": "llamacpp",
    }
