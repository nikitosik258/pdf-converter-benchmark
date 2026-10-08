from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_benchmark.benchmark.experiment import FullExperimentRunner, run_preflight


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Run the complete 10-tool x 5-document benchmark with preflight, per-tool integrity checks, and cleaned result datasets."
    )
    parser.add_argument("--benchmark-config", type=Path, default=root / "config" / "benchmark.yaml")
    parser.add_argument("--experiment-config", type=Path, default=root / "config" / "experiment.yaml")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--experiment-id", default=None)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--force-api", action="store_true", help="Explicitly allow fresh cloud API calls even if raw cache exists.")
    parser.add_argument("--force-rerun", action="store_true", help="Re-run local adapters; cloud raw is still protected unless --force-api is set.")
    parser.add_argument("--reuse-stale-raw", action="store_true")
    parser.add_argument("--stop-after-corrupt-tool", action="store_true")
    args = parser.parse_args()

    if args.preflight_only:
        report = run_preflight(root, args.benchmark_config, args.experiment_config)
        target = root / "outputs" / "benchmark" / "preflight_latest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\nSaved: {target}")
        raise SystemExit(0 if report["status"] == "pass" else 2)

    runner = FullExperimentRunner(
        project_root=root,
        benchmark_config_path=args.benchmark_config,
        experiment_config_path=args.experiment_config,
        seed=args.seed,
        force_api=args.force_api,
        force_rerun=args.force_rerun,
        reuse_stale_raw=args.reuse_stale_raw,
        stop_after_corrupt_tool=args.stop_after_corrupt_tool,
    )
    manifest = runner.run(args.experiment_id)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if manifest["status"] == "preflight_failed":
        raise SystemExit(2)
    if manifest["status"] != "clean":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
