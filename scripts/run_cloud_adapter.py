from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from dotenv import load_dotenv

from pdf_benchmark.adapters.cloud import (
    OCRSpaceAdapter,
    NutrientDataExtractionAdapter,
    MindeeOCRAdapter,
    AdobeExtractAdapter,
    AzureDocumentIntelligenceAdapter,
    LlamaParseAdapter,
)

REGISTRY = {
    "ocr_space": OCRSpaceAdapter,
    "nutrient": NutrientDataExtractionAdapter,
    "mindee": MindeeOCRAdapter,
    "adobe_extract": AdobeExtractAdapter,
    "llamaparse": LlamaParseAdapter,
    "azure_document_intelligence": AzureDocumentIntelligenceAdapter,
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)

    parser = argparse.ArgumentParser()
    parser.add_argument("tool", choices=sorted(REGISTRY))
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--output-root", type=Path, default=root / "outputs" / "cloud")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--mock-fixture", type=Path, default=None)
    args = parser.parse_args()

    config_path = args.config or root / "config" / "tools" / f"{args.tool}.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if args.mock:
        if not args.mock_fixture:
            raise SystemExit("--mock requires --mock-fixture")
        config["mock"] = True
        config["mock_fixture"] = str(args.mock_fixture.resolve())

    document_id = args.document_id or args.pdf.stem
    output_dir = args.output_root / args.tool / document_id
    result = REGISTRY[args.tool](config).convert(
        args.pdf,
        output_dir,
        document_id=document_id,
        raise_on_error=True,
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
