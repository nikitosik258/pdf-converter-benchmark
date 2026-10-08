from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_benchmark.benchmark import BenchmarkConfig, BenchmarkRunner, TOOL_SPECS
from pdf_benchmark.benchmark.config import resolve_document_path


def _flatten(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    out: list[str] = []
    for value in values:
        out.extend(x.strip() for x in value.split(",") if x.strip())
    return out


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description=(
            "Run one tool/PDF, one tool/all PDFs, all tools/one PDF, "
            "or the complete PDF benchmark."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / "config" / "benchmark.yaml",
    )
    parser.add_argument(
        "--tool",
        action="append",
        help="Tool name; repeat or comma-separate. Omit for all configured tools.",
    )
    parser.add_argument(
        "--document",
        action="append",
        help="Document ID; repeat or comma-separate. Omit for all configured PDFs.",
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--run-id", default=None)

    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help=(
            "Re-run local tool execution. For cloud tools with existing raw data, "
            "this only re-standardizes raw unless --force-api is also given."
        ),
    )
    parser.add_argument(
        "--force-api",
        action="store_true",
        help=(
            "Explicitly permit a cloud API to be called again even when raw "
            "response/cache already exists."
        ),
    )
    parser.add_argument(
        "--reuse-stale-raw",
        action="store_true",
        help=(
            "If cloud tool config/PDF fingerprint changed, intentionally reuse "
            "the old vendor raw response rather than calling the API."
        ),
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--mock-cloud",
        action="store_true",
        help="Use Prompt-8 mock fixtures for cloud adapters; no network calls.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    config = BenchmarkConfig.from_yaml(args.config)
    requested_tools = _flatten(args.tool) or list(config.tools)
    requested_documents = _flatten(args.document) or [
        d.document_id for d in config.documents
    ]

    if args.list:
        print("Tools:")
        for tool in config.tools:
            spec = TOOL_SPECS[tool]
            print(f"  {tool:<32} {spec.kind:<5} {spec.environment}")
        print("\nDocuments:")
        for doc in config.documents:
            try:
                path = resolve_document_path(project_root, doc)
                status = str(path)
            except FileNotFoundError:
                status = "NOT FOUND"
            print(f"  {doc.document_id:<5} {doc.filename:<16} {status}")
        return

    if args.dry_run:
        print(
            json.dumps(
                {
                    "tools": requested_tools,
                    "documents": requested_documents,
                    "pairs": [
                        {"tool": tool, "document_id": doc}
                        for tool in requested_tools
                        for doc in requested_documents
                    ],
                    "pair_count": len(requested_tools) * len(requested_documents),
                    "seed": args.seed if args.seed is not None else config.seed,
                    "mock_cloud": args.mock_cloud,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    runner = BenchmarkRunner(
        project_root=project_root,
        config=config,
        seed=args.seed,
        force_rerun=args.force_rerun,
        force_api=args.force_api,
        reuse_stale_raw=args.reuse_stale_raw,
        fail_fast=args.fail_fast,
        mock_cloud=args.mock_cloud,
    )
    manifest = runner.run(
        tools=requested_tools,
        documents=requested_documents,
        run_id=args.run_id,
    )

    print("\nBenchmark run finished")
    print(f"run_id: {manifest['run_id']}")
    print(f"status: {manifest['status']}")
    print(
        f"pairs: {manifest['pair_count_success']}/"
        f"{manifest['pair_count_expected']} successful"
    )
    print(
        Path(config.output_root)
        / "runs"
        / manifest["run_id"]
        / "run_manifest.json"
    )

    if manifest["status"] == "failed":
        raise SystemExit(2)
    if manifest["status"] == "partial":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
