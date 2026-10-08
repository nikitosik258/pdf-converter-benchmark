from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from pdf_benchmark.benchmark.worker import run_adapter_worker


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Internal adapter worker. Usually invoked by run_benchmark.py."
    )
    parser.add_argument("--tool", required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--raw-cache-dir",
        type=Path,
        help="Existing acquisition cache to read in --standardize-only mode.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--standardize-only", action="store_true")
    parser.add_argument("--mock-cloud", action="store_true")
    parser.add_argument(
        "--config-override-json",
        help="Internal JSON object of runtime-only adapter configuration overrides.",
    )
    args = parser.parse_args()

    config_overrides = None
    if args.config_override_json:
        candidate = json.loads(args.config_override_json)
        if not isinstance(candidate, dict):
            raise ValueError("--config-override-json must be a JSON object")
        config_overrides = candidate

    project_root = Path(__file__).resolve().parents[1]
    if load_dotenv is not None and os.getenv("PDF_BENCHMARK_DISABLE_DOTENV") != "1":
        load_dotenv(project_root / ".env", override=False)

    result = run_adapter_worker(
        project_root=project_root,
        tool=args.tool,
        pdf_path=args.pdf.resolve(),
        document_id=args.document_id,
        output_dir=args.output_dir.resolve(),
        raw_cache_dir=(args.raw_cache_dir.resolve() if args.raw_cache_dir else None),
        seed=args.seed,
        standardize_only=args.standardize_only,
        mock_cloud=args.mock_cloud,
        config_overrides=config_overrides,
    )
    print(
        f"{result.get('tool', args.tool)}/{args.document_id}: "
        f"{result.get('status', 'unknown')}"
    )


if __name__ == "__main__":
    main()
