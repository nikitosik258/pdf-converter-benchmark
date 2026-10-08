from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psutil

from pdf_benchmark.benchmark.registry import TOOL_SPECS, environment_python
from pdf_benchmark.models import StandardizedDocument

from .config import WebSettings
from .converters import ConverterRegistry
from .errors import ConversionExecutionError


logger = logging.getLogger("pdf_benchmark.web.runner")


# These settings apply only to interactive web conversions.  They are passed
# to the worker explicitly and never change the fixed benchmark configuration.
# The GPU image includes RapidOCR's torch backend but not the engines selected
# by Docling's automatic OCR chooser for a mixed Russian/English request.
WEB_ADAPTER_CONFIG_OVERRIDES: dict[str, dict[str, object]] = {
    "docling": {
        "ocr_engine": "rapidocr",
        "ocr_languages": ["cyrillic"],
        "ocr_backend": "torch",
        # Keep the interactive single-GPU demo within consumer GPU memory.
        # The benchmark configuration remains unchanged and retains its full
        # picture/chart/formula enrichment pipeline.
        "do_formula_enrichment": False,
        "do_picture_classification": False,
        "do_chart_extraction": False,
    }
}


@dataclass(frozen=True)
class RunnerResult:
    document: StandardizedDocument
    warnings: list[str]
    processing_time_seconds: float


class AdapterRunner(Protocol):
    def run(
        self,
        *,
        converter_id: str,
        document_id: str,
        pdf_path: Path,
        output_dir: Path,
    ) -> RunnerResult: ...


class SubprocessAdapterRunner:
    """Invoke the existing benchmark adapter worker in its pinned environment."""

    _provider_secrets = {
        "OCR_SPACE_API_KEY",
        "NUTRIENT_DWS_EXTRACTION_API_KEY",
        "MINDEE_V2_API_KEY",
        "MINDEE_OCR_MODEL_ID",
        "PDF_SERVICES_CLIENT_ID",
        "PDF_SERVICES_CLIENT_SECRET",
        "LLAMA_CLOUD_API_KEY",
        "DOCUMENTINTELLIGENCE_ENDPOINT",
        "DOCUMENTINTELLIGENCE_API_KEY",
    }

    def __init__(self, settings: WebSettings, registry: ConverterRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self._active_processes: set[subprocess.Popen] = set()
        self._process_lock = threading.Lock()
        self._closed = False

    def _environment(self, converter_id: str) -> dict[str, str]:
        source = os.environ.copy()
        allowed = set(self.registry.required_env(converter_id))
        for name in self._provider_secrets - allowed:
            source.pop(name, None)
        source["PDF_BENCHMARK_DISABLE_DOTENV"] = "1"
        source["PDF_BENCHMARK_SEED"] = str(self.settings.seed)
        source["PYTHONHASHSEED"] = str(self.settings.seed)
        return source

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            parent.terminate()
            _, alive = psutil.wait_procs([*children, parent], timeout=5)
            for item in alive:
                item.kill()
        except (psutil.Error, OSError):
            process.kill()

    def run(
        self,
        *,
        converter_id: str,
        document_id: str,
        pdf_path: Path,
        output_dir: Path,
    ) -> RunnerResult:
        spec = TOOL_SPECS[converter_id]
        python_executable = environment_python(self.settings.project_root, spec.environment)
        worker_script = self.settings.project_root / "scripts" / "benchmark_worker.py"
        command = [
            str(python_executable),
            str(worker_script),
            "--tool",
            converter_id,
            "--pdf",
            str(pdf_path),
            "--document-id",
            document_id,
            "--output-dir",
            str(output_dir),
            "--seed",
            str(self.settings.seed),
        ]
        if overrides := WEB_ADAPTER_CONFIG_OVERRIDES.get(converter_id):
            command.extend(["--config-override-json", json.dumps(overrides)])
        output_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = output_dir / "worker.stdout.log"
        stderr_path = output_dir / "worker.stderr.log"
        started = time.monotonic()

        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr:
            process = subprocess.Popen(
                command,
                cwd=self.settings.project_root,
                env=self._environment(converter_id),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                text=True,
                shell=False,
            )
            with self._process_lock:
                if self._closed:
                    self._terminate_tree(process)
                    raise ConversionExecutionError(
                        "SERVER_STOPPING",
                        "The conversion was stopped because the server is shutting down.",
                        retryable=True,
                    )
                self._active_processes.add(process)
            try:
                return_code = process.wait(timeout=self.settings.adapter_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                self._terminate_tree(process)
                raise ConversionExecutionError(
                    "PARSER_TIMEOUT",
                    "The converter exceeded its processing time limit.",
                    retryable=False,
                ) from exc
            finally:
                with self._process_lock:
                    self._active_processes.discard(process)

        duration = time.monotonic() - started
        result_path = output_dir / "adapter_result.json"
        standardized_path = output_dir / "standardized.json"
        payload: dict = {}
        if result_path.exists():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}

        if return_code != 0 or payload.get("status") == "failed" or not standardized_path.exists():
            logger.error(
                "adapter worker failed",
                extra={
                    "converter_id": converter_id,
                    "document_id": document_id,
                    "return_code": return_code,
                    "adapter_error_type": payload.get("error_type"),
                },
            )
            raise ConversionExecutionError(
                "ACQUISITION_FAILED",
                "The converter could not process this PDF.",
                retryable=False,
            )

        try:
            document = StandardizedDocument.model_validate_json(
                standardized_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ConversionExecutionError(
                "STANDARDIZATION_FAILED",
                "The converter output could not be standardized.",
                retryable=True,
            ) from exc

        warnings = payload.get("warnings")
        return RunnerResult(
            document=document,
            warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
            processing_time_seconds=duration,
        )

    def shutdown(self) -> None:
        """Stop child adapter processes before the executor is joined."""
        with self._process_lock:
            self._closed = True
            active = list(self._active_processes)
        for process in active:
            self._terminate_tree(process)
