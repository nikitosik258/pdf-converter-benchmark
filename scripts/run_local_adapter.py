from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from pdf_benchmark.adapters.local import create_local_adapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tool", choices=["pymupdf", "pdfplumber", "docling", "pdfminer", "mineru"])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/local"))
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "tools" / f"{args.tool}.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))["config"]
    adapter = create_local_adapter(args.tool, config=config)
    out = args.output_root / args.tool / (args.document_id or args.pdf.stem)
    result = adapter.convert(args.pdf, out, document_id=args.document_id, raise_on_error=False)
    print(result.model_dump_json(indent=2, exclude={"document"}))


if __name__ == "__main__":
    main()

