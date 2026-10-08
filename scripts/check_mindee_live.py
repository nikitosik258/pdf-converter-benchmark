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


def _redacted_id(value: str) -> str:
    if not value:
        return "missing"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _make_one_page_pdf(source: Path, target: Path, page_index: int) -> None:
    try:
        import pymupdf
    except Exception as exc:
        raise RuntimeError(
            "PyMuPDF is required in .venv-cloud for the one-page smoke test."
        ) from exc

    src = pymupdf.open(source)
    try:
        if page_index < 0 or page_index >= src.page_count:
            raise ValueError(
                f"page_index={page_index} outside PDF with {src.page_count} pages"
            )
        out = pymupdf.open()
        try:
            out.insert_pdf(src, from_page=page_index, to_page=page_index)
            out.save(target, garbage=4, deflate=True)
        finally:
            out.close()
    finally:
        src.close()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)

    parser = argparse.ArgumentParser(
        description=(
            "Validate Mindee V2 OCR model + API key with a single-page live "
            "request. The script consumes exactly one page when a fresh request "
            "is made."
        )
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=root / "documents" / "369.pdf",
        help="Source PDF for the one-page smoke test.",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="1-based page number to send. Default: 1.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Repeat the live request even if a previous SUCCESS marker exists.",
    )
    args = parser.parse_args()

    api_key = os.getenv("MINDEE_V2_API_KEY", "")
    model_id = os.getenv("MINDEE_OCR_MODEL_ID", "")
    if not api_key or not model_id:
        raise SystemExit(
            "Missing MINDEE_V2_API_KEY and/or MINDEE_OCR_MODEL_ID in .env"
        )

    try:
        sdk_version = version("mindee")
    except PackageNotFoundError:
        sdk_version = "not-installed"

    # Verify imports before spending one page.
    try:
        from mindee import FileInput, PollingOptions  # noqa: F401
        try:
            from mindee.v2 import Client  # noqa: F401
            client_import = "mindee.v2.Client"
        except ImportError:
            from mindee import Client  # noqa: F401
            client_import = "mindee.Client (compatibility fallback)"
        from mindee.v2.product.ocr import OCRParameters, OCRResponse  # noqa: F401
    except Exception as exc:
        raise SystemExit(
            "Mindee SDK import check failed before any API call: "
            f"{type(exc).__name__}: {exc}"
        )

    pdf = args.pdf.resolve()
    if not pdf.exists():
        raise SystemExit(f"PDF not found: {pdf}")

    smoke_root = root / "outputs" / "cloud_smoke" / "mindee"
    success_marker = smoke_root / "SUCCESS.json"

    if success_marker.exists() and not args.force:
        payload = json.loads(success_marker.read_text(encoding="utf-8"))
        print(json.dumps({
            "status": "already_passed",
            "message": (
                "Previous live Mindee smoke test already passed. "
                "No API call was made."
            ),
            **payload,
        }, ensure_ascii=False, indent=2))
        return

    # Import only after all no-network checks have passed.
    from pdf_benchmark.adapters.cloud.mindee_adapter import MindeeOCRAdapter

    config_path = root / "config" / "tools" / "mindee.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config["mock"] = False

    tmp_dir = Path(tempfile.mkdtemp(prefix="mindee_smoke_"))
    try:
        one_page = tmp_dir / "mindee_smoke_page.pdf"
        _make_one_page_pdf(pdf, one_page, args.page - 1)

        run_dir = smoke_root / "latest"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        result = MindeeOCRAdapter(config).convert(
            one_page,
            run_dir,
            document_id="MINDEE_SMOKE",
            run_id="mindee_live_smoke",
            raise_on_error=True,
        )

        if result.document is None or len(result.document.pages) != 1:
            raise RuntimeError(
                "Mindee live request returned no standardized page."
            )

        page = result.document.pages[0]
        text = "\n".join(block.raw_text for block in page.text_blocks).strip()
        word_count = sum(
            len((block.provenance or {}).get("words") or [])
            for block in page.text_blocks
        )

        if not text:
            raise RuntimeError(
                "Mindee returned an OCR page but the page text is empty."
            )

        payload = {
            "status": "pass",
            "network_call_made": True,
            "pages_consumed": 1,
            "sdk_version": sdk_version,
            "client_import": client_import,
            "model_id_fingerprint": _redacted_id(model_id),
            "ocr_text_characters": len(text),
            "ocr_words_with_geometry": word_count,
            "standardized_pages": len(result.document.pages),
            "output_dir": str(run_dir),
        }
        success_marker.parent.mkdir(parents=True, exist_ok=True)
        success_marker.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
