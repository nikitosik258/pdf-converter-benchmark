from __future__ import annotations

import argparse
from pathlib import Path

from pdf_benchmark.normalization.pipeline import normalize_document_json


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize a standardized benchmark document before matching/evaluation."
    )
    parser.add_argument("input_json", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/normalization.yaml"),
    )
    args = parser.parse_args()

    target = normalize_document_json(
        args.input_json,
        args.output_json,
        args.config,
    )
    print(target)


if __name__ == "__main__":
    main()
