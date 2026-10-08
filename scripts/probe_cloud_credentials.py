from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv


def env_ready(*names: str) -> tuple[bool, str]:
    missing = [name for name in names if not os.getenv(name)]
    return (not missing, "ready" if not missing else "missing: " + ", ".join(missing))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    payload = {}

    for tool, names in {
        "ocr_space": ("OCR_SPACE_API_KEY",),
        "nutrient": ("NUTRIENT_DWS_EXTRACTION_API_KEY",),
        "llamaparse": ("LLAMA_CLOUD_API_KEY",),
        "mindee": ("MINDEE_V2_API_KEY", "MINDEE_OCR_MODEL_ID"),
        "adobe_extract": ("PDF_SERVICES_CLIENT_ID", "PDF_SERVICES_CLIENT_SECRET"),
        "azure_document_intelligence": ("DOCUMENTINTELLIGENCE_ENDPOINT", "DOCUMENTINTELLIGENCE_API_KEY"),
    }.items():
        ready, detail = env_ready(*names)
        payload[tool] = {"ready": ready, "detail": detail}

    # Never print credential values.
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
