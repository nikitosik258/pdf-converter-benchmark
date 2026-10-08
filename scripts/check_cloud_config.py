\
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
import yaml


GROUPS = {
    "ocr_space": ["OCR_SPACE_API_KEY"],
    "nutrient": ["NUTRIENT_DWS_EXTRACTION_API_KEY"],
    "llamaparse": ["LLAMA_CLOUD_API_KEY"],
    "mindee": ["MINDEE_V2_API_KEY", "MINDEE_OCR_MODEL_ID"],
    "adobe_extract": ["PDF_SERVICES_CLIENT_ID", "PDF_SERVICES_CLIENT_SECRET"],
    "azure_document_intelligence": ["DOCUMENTINTELLIGENCE_ENDPOINT", "DOCUMENTINTELLIGENCE_API_KEY"],
}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    payload = yaml.safe_load((root / "config" / "benchmark.yaml").read_text(encoding="utf-8"))
    active = set(payload["benchmark"]["tools"])

    for tool, names in GROUPS.items():
        state = "ACTIVE" if tool in active else "INACTIVE (preserved)"
        print(f"\n{tool} [{state}]")
        for name in names:
            print(f"  {name:<42} {'SET' if os.getenv(name) else 'MISSING'}")


if __name__ == "__main__":
    main()
