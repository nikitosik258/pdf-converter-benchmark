\
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml
from dotenv import load_dotenv


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _make_one_page_pdf(source: Path, target: Path, page_index: int) -> None:
    import pymupdf
    src = pymupdf.open(source)
    try:
        if page_index < 0 or page_index >= src.page_count:
            raise ValueError(f"page_index={page_index} outside PDF with {src.page_count} pages")
        dst = pymupdf.open()
        try:
            dst.insert_pdf(src, from_page=page_index, to_page=page_index)
            dst.save(target, garbage=4, deflate=True)
        finally:
            dst.close()
    finally:
        src.close()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    parser = argparse.ArgumentParser(description="One-page quota-safe LlamaParse live smoke test")
    parser.add_argument("--pdf", type=Path, default=root / "documents" / "369.pdf")
    parser.add_argument("--page", type=int, default=1, help="1-based source page; default 1")
    parser.add_argument("--force", action="store_true", help="repeat a real API call even after a prior pass")
    args = parser.parse_args()

    key = os.getenv("LLAMA_CLOUD_API_KEY", "")
    if not key:
        raise SystemExit("Missing LLAMA_CLOUD_API_KEY in .env")
    try:
        sdk_version = version("llama-cloud")
    except PackageNotFoundError:
        raise SystemExit("llama-cloud is not installed in .venv-cloud")

    try:
        from llama_cloud import LlamaCloud  # noqa: F401
    except Exception as exc:
        raise SystemExit(f"llama-cloud import failed before any API call: {type(exc).__name__}: {exc}")

    source = args.pdf.resolve()
    if not source.exists():
        raise SystemExit(f"PDF not found: {source}")

    smoke_root = root / "outputs" / "cloud_smoke" / "llamaparse"
    marker = smoke_root / "SUCCESS.json"
    if marker.exists() and not args.force:
        old = json.loads(marker.read_text(encoding="utf-8"))
        print(json.dumps({
            "status": "already_passed",
            "message": "Previous LlamaParse smoke passed; no API call was made.",
            **old,
        }, ensure_ascii=False, indent=2))
        return

    from pdf_benchmark.adapters.cloud.llamaparse_adapter import LlamaParseAdapter

    config_path = root / "config" / "tools" / "llamaparse.yaml"
    raw_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = dict(raw_cfg.get("config", raw_cfg))
    config["mock"] = False

    tmp = Path(tempfile.mkdtemp(prefix="llamaparse_smoke_"))
    try:
        one = tmp / "one_page.pdf"
        _make_one_page_pdf(source, one, args.page - 1)
        run_dir = smoke_root / "latest"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        result = LlamaParseAdapter(config).convert(
            one, run_dir, document_id="LLAMAPARSE_SMOKE", run_id="llamaparse_live_smoke", raise_on_error=True
        )
        if result.document is None or len(result.document.pages) != 1:
            raise RuntimeError("LlamaParse did not produce exactly one standardized smoke page")
        page = result.document.pages[0]
        object_count = sum((
            len(page.text_blocks), len(page.tables), len(page.formulas),
            len(page.images), len(page.diagrams), len(page.chemical_objects)
        ))
        if object_count == 0:
            raise RuntimeError("LlamaParse produced no standardized objects")
        meta = result.raw_result.metadata if result.raw_result else {}
        payload = {
            "status": "pass",
            "network_call_made": True,
            "pages_consumed": 1,
            "credits_estimate": meta.get("credits_estimate", 10),
            "provider_credits": meta.get("provider_credits"),
            "sdk_version": sdk_version,
            "api_key_fingerprint": _fingerprint(key),
            "tier": meta.get("tier"),
            "version": meta.get("version"),
            "standardized_pages": len(result.document.pages),
            "standardized_objects": object_count,
            "output_dir": str(run_dir),
        }
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
