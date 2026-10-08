from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any

import yaml

from pdf_benchmark.models import RawToolResult
from pdf_benchmark.standardization import enrich_chemical_objects, enrich_math_formulas
from pdf_benchmark.utils.io import ensure_dir, write_json
from pdf_benchmark.utils.resources import ResourceMonitor

from .cache import PairCache
from .registry import TOOL_SPECS


def _portable_raw_path(value: str | None, raw_cache: PairCache) -> Path | None:
    """Resolve a cached raw path after the repository has been moved.

    Legacy snapshots may contain an absolute path from the machine that
    acquired the raw response. The part below the cache's ``raw`` directory is
    stable, so use it to locate the preserved artifact in the current cache.
    """
    if not value:
        return None

    direct = Path(value)
    if direct.exists():
        return direct.resolve()

    normalized = str(value).replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    raw_positions = [
        index for index, part in enumerate(parts) if part.casefold() == "raw"
    ]
    candidates: list[Path] = []
    if raw_positions:
        tail = parts[raw_positions[-1] + 1 :]
        if tail:
            candidates.append(raw_cache.raw_dir.joinpath(*tail))
    if not Path(normalized).is_absolute():
        if parts and parts[0].casefold() == "raw":
            candidates.append(raw_cache.root.joinpath(*parts))
        elif parts:
            candidates.append(raw_cache.raw_dir.joinpath(*parts))
    if parts:
        candidates.append(raw_cache.raw_dir / parts[-1])

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _rebase_raw_result(raw: RawToolResult, raw_cache: PairCache) -> bool:
    """Rebase path-bearing fields without modifying the preserved snapshot."""
    rebased = False

    primary = _portable_raw_path(raw.primary_artifact, raw_cache)
    if primary is not None and str(primary) != raw.primary_artifact:
        raw.primary_artifact = str(primary)
        rebased = True

    artifacts: list[str] = []
    for value in raw.artifacts:
        path = _portable_raw_path(value, raw_cache)
        replacement = str(path) if path is not None else value
        artifacts.append(replacement)
        rebased = rebased or replacement != value
    raw.artifacts = artifacts

    saved_dir = raw.metadata.get("saved_dir")
    if isinstance(saved_dir, str):
        path = _portable_raw_path(saved_dir, raw_cache)
        if path is not None and str(path) != saved_dir:
            raw.metadata["saved_dir"] = str(path)
            rebased = True

    return rebased


def seed_everything(seed: int) -> None:
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def load_tool_config(project_root: Path, tool: str) -> dict[str, Any]:
    spec = TOOL_SPECS[tool]
    path = project_root / "config" / "tools" / spec.config_filename
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(payload.get("config", payload))


def create_adapter(tool: str, config: dict[str, Any]):
    spec = TOOL_SPECS[tool]
    if spec.kind == "local":
        from pdf_benchmark.adapters.local import create_local_adapter
        return create_local_adapter(tool, config=config)

    from pdf_benchmark.adapters.cloud import (
        AdobeExtractAdapter,
        AzureDocumentIntelligenceAdapter,
        LlamaParseAdapter,
        NutrientDataExtractionAdapter,
        MindeeOCRAdapter,
        OCRSpaceAdapter,
    )

    classes = {
        "ocr_space": OCRSpaceAdapter,
        "nutrient": NutrientDataExtractionAdapter,
        "mindee": MindeeOCRAdapter,
        "adobe_extract": AdobeExtractAdapter,
        "llamaparse": LlamaParseAdapter,
        "azure_document_intelligence": AzureDocumentIntelligenceAdapter,
    }
    return classes[tool](config=config)


def run_adapter_worker(
    *,
    project_root: Path,
    tool: str,
    pdf_path: Path,
    document_id: str,
    output_dir: Path,
    raw_cache_dir: Path | None = None,
    seed: int,
    standardize_only: bool = False,
    mock_cloud: bool = False,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    seed_everything(seed)

    # Prompt 12: benchmark workers run in isolated environments. Load local
    # development credentials from .env inside the worker so cloud adapters do
    # not depend on the parent PowerShell session exporting every variable.
    if os.getenv("PDF_BENCHMARK_DISABLE_DOTENV") != "1":
        try:
            from dotenv import load_dotenv
            load_dotenv(project_root / ".env", override=False)
        except Exception:
            # Local environments intentionally do not need python-dotenv.
            pass

    cache = PairCache(output_dir).ensure()
    raw_cache = PairCache(raw_cache_dir or output_dir).ensure()
    config = load_tool_config(project_root, tool)
    if config_overrides:
        config.update(config_overrides)

    if mock_cloud:
        spec = TOOL_SPECS[tool]
        if spec.kind != "cloud":
            raise ValueError("--mock-cloud can only be used with cloud tools")
        fixture = project_root / "tests" / "fixtures" / "cloud" / {
            "ocr_space": "ocr_space.json",
            "nutrient": "nutrient.json",
            "mindee": "mindee.json",
            "adobe_extract": "adobe.json",
            "llamaparse": "llamaparse.json",
            "azure_document_intelligence": "azure.json",
        }[tool]
        config["mock"] = True
        config["mock_fixture"] = str(fixture)

    adapter = create_adapter(tool, config)

    if standardize_only:
        if not raw_cache.raw_snapshot.exists():
            raise FileNotFoundError(
                f"Cannot standardize-only: {raw_cache.raw_snapshot} does not exist"
            )
        raw = RawToolResult.model_validate_json(
            raw_cache.raw_snapshot.read_text(encoding="utf-8")
        )
        paths_rebased = _rebase_raw_result(raw, raw_cache)
        with ResourceMonitor(
            interval=float(config.get("resource_sample_interval", 0.1))
        ) as monitor:
            doc = adapter.standardize(
                raw,
                raw_cache.raw_dir,
                cache.assets_dir,
                document_id=document_id,
                pdf_path=pdf_path,
            )
            doc = enrich_chemical_objects(
                doc,
                raw_dir=raw_cache.raw_dir,
                assets_dir=cache.assets_dir,
            )
            doc = enrich_math_formulas(doc)
        doc.raw_artifacts = sorted(set(raw.artifacts))
        doc.metadata.update(
            {
                "resume_standardize_only": True,
                "raw_snapshot_paths_rebased": paths_rebased,
                "benchmark_seed": seed,
            }
        )
        write_json(cache.standardized, doc.model_dump(mode="json"))
        payload = {
            "status": "success",
            "tool": tool,
            "document_id": document_id,
            "standardize_only": True,
            "resource_usage": monitor.result().model_dump(mode="json"),
            "raw_result": raw.model_dump(mode="json"),
            "standardized_path": str(cache.standardized),
            "tool_metadata": adapter.tool_metadata().model_dump(mode="json"),
        }
        write_json(cache.adapter_result, payload)
        return payload

    result = adapter.convert(
        pdf_path,
        output_dir,
        document_id=document_id,
        raise_on_error=False,
    )
    payload = result.model_dump(mode="json", exclude={"document"})
    payload["standardize_only"] = False
    payload["benchmark_seed"] = seed
    write_json(cache.adapter_result, payload)

    if result.raw_result is not None:
        write_json(
            cache.raw_snapshot,
            result.raw_result.model_dump(mode="json"),
        )

    return payload
