from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
from pathlib import Path

PACKAGE = {
    "pymupdf": ("PyMuPDF", "pymupdf"),
    "pdfplumber": ("pdfplumber", "pdfplumber"),
    "docling": ("docling", "docling"),
    "pdfminer": ("pdfminer.six", "pdfminer"),
    "marker": ("marker-pdf", "marker"),
    "mineru": ("mineru", "mineru"),
    "ocr_space": ("requests", "requests"),
    "nutrient": ("nutrient-dws", "nutrient_dws"),
    "llamaparse": ("llama-cloud", "llama_cloud"),
    "mindee": ("mindee", "mindee"),
    "adobe_extract": ("pdfservices-sdk", "adobe.pdfservices"),
    "azure_document_intelligence": ("azure-ai-documentintelligence", "azure.ai.documentintelligence"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True, choices=sorted(PACKAGE))
    args = parser.parse_args()
    distribution, module = PACKAGE[args.tool]
    importlib.import_module("pdf_benchmark")
    importlib.import_module(module)
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = None
    payload = {
        "tool": args.tool,
        "distribution": distribution,
        "package_version": version,
        "module": module,
        "import_ok": True,
    }
    if args.tool == "marker":
        root = Path(__file__).resolve().parents[1]
        marker_path = root / ".tools" / "llama.cpp" / "llama-server.path"
        binary = marker_path.read_text(encoding="utf-8-sig").strip() if marker_path.exists() else ""
        payload["llama_server_path"] = binary
        payload["llama_server_exists"] = bool(binary and Path(binary).exists())
        if not payload["llama_server_exists"]:
            raise SystemExit("Marker llama-server runtime is missing")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
