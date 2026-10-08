from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml
from dotenv import load_dotenv


def make_one_page_pdf(source: Path, target: Path, page_index: int) -> None:
    import pymupdf
    src = pymupdf.open(source)
    try:
        if page_index < 0 or page_index >= src.page_count:
            raise ValueError(f"page_index={page_index} outside PDF with {src.page_count} pages")
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=root / "documents" / "369.pdf")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not os.getenv("NUTRIENT_DWS_EXTRACTION_API_KEY"):
        raise SystemExit("Missing NUTRIENT_DWS_EXTRACTION_API_KEY in .env")

    try:
        sdk_version = version("nutrient-dws")
    except PackageNotFoundError:
        raise SystemExit("nutrient-dws is not installed in .venv-cloud")

    smoke_root = root / "outputs" / "cloud_smoke" / "nutrient"
    success_marker = smoke_root / "SUCCESS.json"
    if success_marker.exists() and not args.force:
        previous = json.loads(success_marker.read_text(encoding="utf-8"))
        print(json.dumps({
            "status": "already_passed",
            "message": "Previous Nutrient smoke test already passed; no API call was made.",
            **previous,
            "network_call_made": False,
            "credits_consumed_this_run": 0,
        }, ensure_ascii=False, indent=2))
        return

    from pdf_benchmark.adapters.cloud.nutrient_adapter import NutrientDataExtractionAdapter

    cfg = yaml.safe_load((root / "config" / "tools" / "nutrient.yaml").read_text(encoding="utf-8")) or {}
    cfg["mock"] = False

    tmp_dir = Path(tempfile.mkdtemp(prefix="nutrient_smoke_"))
    try:
        one_page = tmp_dir / "smoke.pdf"
        make_one_page_pdf(args.pdf.resolve(), one_page, args.page - 1)
        run_dir = smoke_root / "latest"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        result = NutrientDataExtractionAdapter(cfg).convert(
            one_page,
            run_dir,
            document_id="NUTRIENT_SMOKE",
            run_id="nutrient_live_smoke",
            raise_on_error=True,
        )
        if result.document is None:
            raise RuntimeError("Nutrient produced no standardized document")
        object_count = sum(
            len(p.text_blocks) + len(p.tables) + len(p.formulas) + len(p.images) + len(p.diagrams)
            for p in result.document.pages
        )
        if object_count <= 0:
            raise RuntimeError("Nutrient response contained no standardized objects")

        raw_meta = result.raw_result.metadata if result.raw_result else {}
        payload = {
            "status": "pass",
            "network_call_made": True,
            "pages_consumed": 1,
            "credits_estimate": raw_meta.get("credits_estimate"),
            "sdk_version": sdk_version,
            "standardized_pages": len(result.document.pages),
            "standardized_objects": object_count,
            "output_dir": str(run_dir),
        }
        success_marker.parent.mkdir(parents=True, exist_ok=True)
        success_marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
