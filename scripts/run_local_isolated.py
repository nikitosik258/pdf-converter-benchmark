from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ENV_BY_TOOL = {
    "pymupdf": ".venv",
    "pdfplumber": ".venv",
    "pdfminer": ".venv",
    "docling": ".venv",
    "marker": ".venv-marker",
    "mineru": ".venv-mineru",
}


def _marker_runtime_env(root: Path, env: dict[str, str]) -> None:
    path_file = root / ".tools" / "llama.cpp" / "llama-server.path"
    if not path_file.exists():
        raise SystemExit(
            "Marker llama.cpp runtime is missing. Run:\n"
            "powershell -ExecutionPolicy Bypass -File scripts/setup_marker_llama.ps1"
        )

    llama_server = Path(path_file.read_text(encoding="utf-8-sig").strip())
    if not llama_server.exists():
        raise SystemExit(f"Marker llama-server does not exist: {llama_server}")

    env["LLAMA_CPP_BINARY"] = str(llama_server)
    env["SURYA_INFERENCE_BACKEND"] = "llamacpp"
    env["SURYA_INFERENCE_KEEP_ALIVE"] = "1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a local adapter using the benchmark's isolated tool environment."
    )
    parser.add_argument("tool", choices=sorted(ENV_BY_TOOL))
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/local"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    env_dir = root / ENV_BY_TOOL[args.tool]
    python_exe = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python_exe.exists():
        raise SystemExit(
            f"Environment for {args.tool!r} is missing: {python_exe}. "
            "Run scripts/setup_local_envs.ps1 first."
        )

    cmd = [
        str(python_exe),
        str(root / "scripts" / "run_local_adapter.py"),
        args.tool,
        str(args.pdf),
        "--output-root",
        str(args.output_root),
    ]
    if args.document_id:
        cmd += ["--document-id", args.document_id]

    child_env = os.environ.copy()
    if args.tool == "marker":
        _marker_runtime_env(root, child_env)

    completed = subprocess.run(cmd, cwd=root, env=child_env)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
