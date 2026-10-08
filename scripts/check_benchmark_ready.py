\
from __future__ import annotations

import os
from pathlib import Path

from pdf_benchmark.benchmark import BenchmarkConfig, TOOL_SPECS
from pdf_benchmark.benchmark.config import resolve_document_path
from pdf_benchmark.benchmark.ground_truth import (
    load_ground_truth,
    load_object_manifest,
    validate_ground_truth_manifest,
)
from pdf_benchmark.benchmark.registry import environment_python


CREDENTIAL_GROUPS = {
    "ocr_space": ["OCR_SPACE_API_KEY"],
    "nutrient": ["NUTRIENT_DWS_EXTRACTION_API_KEY"],
    "llamaparse": ["LLAMA_CLOUD_API_KEY"],
    "mindee": ["MINDEE_V2_API_KEY", "MINDEE_OCR_MODEL_ID"],
    "adobe_extract": ["PDF_SERVICES_CLIENT_ID", "PDF_SERVICES_CLIENT_SECRET"],
    # Preserved provider: checked only when it becomes active again.
    "azure_document_intelligence": [
        "DOCUMENTINTELLIGENCE_ENDPOINT",
        "DOCUMENTINTELLIGENCE_API_KEY",
    ],
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = BenchmarkConfig.from_yaml(root / "config" / "benchmark.yaml")
    ok = True

    print("PDF documents")
    for doc in config.documents:
        try:
            path = resolve_document_path(root, doc)
            print(f"  OK      {doc.document_id:<4} {path}")
        except Exception as exc:
            ok = False
            print(f"  MISSING {doc.document_id:<4} {exc}")

    print("\nTool environments")
    for tool in config.tools:
        spec = TOOL_SPECS[tool]
        try:
            python = environment_python(root, spec.environment)
            print(f"  OK      {tool:<30} {python}")
        except Exception as exc:
            ok = False
            print(f"  MISSING {tool:<30} {exc}")

    print("\nGround Truth")
    try:
        objects = load_ground_truth(root / config.ground_truth_dir)
        manifest = load_object_manifest(root / config.object_manifest)
        validate_ground_truth_manifest(objects, manifest)
        print(f"  OK      {len(objects)} objects")
    except Exception as exc:
        ok = False
        print(f"  MISSING/INVALID {exc}")

    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env", override=False)
    except Exception:
        pass

    active_cloud = [t for t in config.tools if TOOL_SPECS[t].kind == "cloud"]
    print("\nActive cloud credential presence (values are never printed)")
    for tool in active_cloud:
        names = CREDENTIAL_GROUPS[tool]
        missing = [name for name in names if not os.getenv(name)]
        if missing:
            ok = False
        print(f"  {tool:<30} " + ("SET" if not missing else "MISSING " + ", ".join(missing)))

    inactive_preserved = [
        tool for tool in CREDENTIAL_GROUPS
        if tool not in active_cloud and tool in TOOL_SPECS and TOOL_SPECS[tool].kind == "cloud"
    ]
    if inactive_preserved:
        print("\nPreserved inactive cloud providers (do not block current benchmark)")
        for tool in inactive_preserved:
            print(f"  {tool:<30} INACTIVE / configuration retained")

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
