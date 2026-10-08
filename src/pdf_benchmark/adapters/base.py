from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import platform
import traceback
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_benchmark.models import (
    AdapterRunResult,
    RawToolResult,
    RunStatus,
    StandardizedDocument,
    ToolMetadata,
)
from pdf_benchmark.standardization import enrich_chemical_objects, enrich_math_formulas
from pdf_benchmark.utils.io import ensure_dir, relative_to_or_name, write_json
from pdf_benchmark.utils.resources import ResourceMonitor


class AdapterError(RuntimeError):
    pass


class ToolExecutionError(AdapterError):
    pass


class ToolOutputParseError(AdapterError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class BaseLocalAdapter(ABC):
    """Base class for local benchmark adapters.

    `run_raw` executes the native tool and persists its native/raw artifacts.
    `standardize` consumes only the persisted raw result and converts it to the
    benchmark schema. This separation is intentional: a standardizer can be
    fixed and re-run without re-running an expensive ML pipeline.
    """

    tool_name: str
    distribution_name: str
    pinned_version: str

    def __init__(self, config: dict[str, Any] | None = None, logger: logging.Logger | None = None):
        self.config = dict(config or {})
        self.logger = logger or logging.getLogger(f"pdf_benchmark.{self.tool_name}")

    def installed_version(self) -> str | None:
        try:
            return importlib.metadata.version(self.distribution_name)
        except importlib.metadata.PackageNotFoundError:
            return None

    def tool_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            tool_name=self.tool_name,
            distribution_name=self.distribution_name,
            version=self.installed_version(),
            configuration=self.config,
            model_versions=self.model_versions(),
        )

    def model_versions(self) -> dict[str, str]:
        return {}

    def _event(self, log_path: Path, level: str, event: str, **payload: Any) -> None:
        record = {
            "timestamp": _utc_now(),
            "level": level,
            "tool": self.tool_name,
            "event": event,
            **payload,
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        getattr(self.logger, level.lower(), self.logger.info)("%s: %s", event, payload)

    @abstractmethod
    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        raise NotImplementedError

    @abstractmethod
    def standardize(
        self,
        raw_result: RawToolResult,
        raw_dir: Path,
        assets_dir: Path,
        *,
        document_id: str,
        pdf_path: Path,
    ) -> StandardizedDocument:
        raise NotImplementedError

    def convert(
        self,
        pdf_path: str | Path,
        output_dir: str | Path,
        *,
        document_id: str | None = None,
        run_id: str | None = None,
        raise_on_error: bool = False,
    ) -> AdapterRunResult:
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)
        document_id = document_id or pdf_path.stem
        run_id = run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"

        out = ensure_dir(output_dir)
        raw_dir = ensure_dir(out / "raw")
        assets_dir = ensure_dir(out / "assets")
        log_path = out / "adapter.log.jsonl"
        run_meta_path = out / "run.json"
        standardized_path = out / "standardized.json"

        started_at = _utc_now()
        actual_version = self.installed_version()
        warnings: list[str] = []
        if actual_version is None:
            warnings.append(f"Distribution {self.distribution_name!r} is not installed.")
        elif actual_version != self.pinned_version:
            warnings.append(
                f"Version mismatch: benchmark pins {self.distribution_name}=={self.pinned_version}, "
                f"but {actual_version} is installed."
            )

        self._event(log_path, "INFO", "adapter_start", run_id=run_id, document_id=document_id)
        raw_result: RawToolResult | None = None
        document: StandardizedDocument | None = None
        status = RunStatus.SUCCESS
        error_type = None
        error_message = None

        with ResourceMonitor(interval=float(self.config.get("resource_sample_interval", 0.1))) as monitor:
            try:
                raw_result = self.run_raw(pdf_path, raw_dir)
                warnings.extend(raw_result.warnings)
                self._event(
                    log_path,
                    "INFO",
                    "raw_complete",
                    run_id=run_id,
                    document_id=document_id,
                    artifacts=len(raw_result.artifacts),
                )
                document = self.standardize(
                    raw_result,
                    raw_dir,
                    assets_dir,
                    document_id=document_id,
                    pdf_path=pdf_path,
                )
                document = enrich_chemical_objects(
                    document,
                    raw_dir=raw_dir,
                    assets_dir=assets_dir,
                )
                document = enrich_math_formulas(document)
                document.raw_artifacts = sorted(set(raw_result.artifacts))
                document.metadata.update(
                    {
                        "run_id": run_id,
                        "source_sha256": _sha256(pdf_path),
                        "adapter_pinned_version": self.pinned_version,
                    }
                )
                write_json(standardized_path, document.model_dump(mode="json"))
                self._event(log_path, "INFO", "standardize_complete", run_id=run_id, document_id=document_id)
            except Exception as exc:
                status = RunStatus.FAILED
                error_type = type(exc).__name__
                error_message = str(exc)
                self._event(
                    log_path,
                    "ERROR",
                    "adapter_failed",
                    run_id=run_id,
                    document_id=document_id,
                    error_type=error_type,
                    error_message=error_message,
                    traceback=traceback.format_exc(),
                )
                if raise_on_error:
                    raise

        usage = monitor.result()
        finished_at = _utc_now()
        metadata = {
            "run_id": run_id,
            "document_id": document_id,
            "tool": self.tool_name,
            "distribution": self.distribution_name,
            "pinned_version": self.pinned_version,
            "installed_version": actual_version,
            "configuration": self.config,
            "status": status.value,
            "started_at": started_at,
            "finished_at": finished_at,
            "source_pdf": str(pdf_path),
            "source_sha256": _sha256(pdf_path),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "resource_usage": usage.model_dump(mode="json"),
            "raw_metadata": raw_result.metadata if raw_result is not None else {},
            "warnings": warnings,
            "error_type": error_type,
            "error_message": error_message,
        }
        write_json(run_meta_path, metadata)
        self._event(
            log_path,
            "INFO" if status == RunStatus.SUCCESS else "ERROR",
            "adapter_finish",
            run_id=run_id,
            document_id=document_id,
            status=status.value,
            wall_time_seconds=usage.wall_time_seconds,
        )

        return AdapterRunResult(
            run_id=run_id,
            document_id=document_id,
            tool_name=self.tool_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            resource_usage=usage,
            raw_result=raw_result,
            standardized_path=relative_to_or_name(standardized_path, out) if document else None,
            run_metadata_path=relative_to_or_name(run_meta_path, out),
            document=document,
            warnings=warnings,
            error_type=error_type,
            error_message=error_message,
        )
