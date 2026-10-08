from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import unicodedata
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from pdf_benchmark.models import StandardizedDocument

from .config import WebSettings
from .converters import ConverterRegistry
from .errors import ConversionExecutionError, WebAPIError
from .exports import public_document_payload, render_text
from .runner import AdapterRunner, SubprocessAdapterRunner
from .schemas import (
    TERMINAL_JOB_STATUSES,
    DocumentRecord,
    DocumentResponse,
    ErrorDetail,
    JobRecord,
    JobResponse,
    JobStatus,
    ResultResponse,
)
from .storage import RuntimeStorage, utc_now


logger = logging.getLogger("pdf_benchmark.web.service")


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(value: str | None) -> str:
    raw = (value or "document.pdf").replace("\\", "/").split("/")[-1]
    normalized = unicodedata.normalize("NFKC", raw)
    cleaned = "".join(
        character if character.isprintable() and character not in '<>:"/\\|?*' else "_"
        for character in normalized
    ).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:120].strip(" .")
    if not cleaned:
        cleaned = "document.pdf"
    stem = cleaned.rsplit(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        cleaned = f"document_{cleaned}"
    if not cleaned.casefold().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned


class WebService:
    def __init__(
        self,
        settings: WebSettings,
        *,
        storage: RuntimeStorage | None = None,
        registry: ConverterRegistry | None = None,
        runner: AdapterRunner | None = None,
    ) -> None:
        self.settings = settings.prepare()
        self.storage = storage or RuntimeStorage(self.settings.storage_root)
        self.registry = registry or ConverterRegistry(self.settings)
        self.runner = runner or SubprocessAdapterRunner(self.settings, self.registry)
        self._executor = ThreadPoolExecutor(
            max_workers=self.settings.executor_workers,
            thread_name_prefix="pdf-converter",
        )
        self._futures: dict[str, Future] = {}
        self._future_lock = threading.Lock()
        self._stopped = False

    @property
    def executor_running(self) -> bool:
        return not self._stopped

    async def upload_document(self, upload: UploadFile) -> DocumentResponse:
        document_id = str(uuid.uuid4())
        document_dir = self.storage.create_document_directory(document_id)
        partial_path = document_dir / "source.pdf.part"
        final_path = self.storage.document_pdf(document_id)
        digest = hashlib.sha256()
        total = 0
        header = bytearray()

        try:
            with partial_path.open("wb") as target:
                while chunk := await upload.read(self.settings.upload_chunk_bytes):
                    total += len(chunk)
                    if total > self.settings.max_upload_bytes:
                        raise WebAPIError(
                            status_code=413,
                            code="LIMIT_FILE_SIZE",
                            message=(
                                "PDF exceeds the configured upload limit of "
                                f"{self.settings.max_upload_bytes} bytes."
                            ),
                        )
                    if len(header) < 1024:
                        header.extend(chunk[: 1024 - len(header)])
                    digest.update(chunk)
                    target.write(chunk)

            if total == 0:
                raise WebAPIError(
                    status_code=422,
                    code="PDF_EMPTY",
                    message="The uploaded file is empty.",
                )
            if b"%PDF-" not in header:
                raise WebAPIError(
                    status_code=422,
                    code="PDF_INVALID",
                    message="The uploaded file does not contain a valid PDF header.",
                )

            partial_path.replace(final_path)
            page_count = await asyncio.to_thread(self._validate_pdf, final_path)
            now = utc_now()
            record = DocumentRecord(
                id=document_id,
                original_filename=safe_filename(upload.filename),
                content_type=upload.content_type,
                size_bytes=total,
                sha256=digest.hexdigest(),
                page_count=page_count,
                created_at=now,
                expires_at=now + timedelta(seconds=self.settings.document_ttl_seconds),
            )
            self.storage.save_document(record)
            logger.info(
                "document uploaded",
                extra={
                    "document_id": document_id,
                    "size_bytes": total,
                    "page_count": page_count,
                },
            )
            return self.document_response(record)
        except Exception:
            self.storage.delete_document(document_id)
            raise
        finally:
            await upload.close()

    def _validate_pdf(self, path: Path) -> int:
        try:
            import pymupdf

            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise WebAPIError(
                        status_code=422,
                        code="PDF_ENCRYPTED",
                        message="Password-protected PDFs are not supported.",
                    )
                page_count = document.page_count
                if page_count < 1:
                    raise WebAPIError(
                        status_code=422,
                        code="PDF_INVALID",
                        message="The PDF contains no pages.",
                    )
                if page_count > self.settings.max_pages:
                    raise WebAPIError(
                        status_code=413,
                        code="LIMIT_PAGE_COUNT",
                        message=(
                            f"PDF has {page_count} pages; the configured limit is "
                            f"{self.settings.max_pages}."
                        ),
                    )
                document.load_page(0)
                return page_count
        except WebAPIError:
            raise
        except Exception as exc:
            raise WebAPIError(
                status_code=422,
                code="PDF_INVALID",
                message="The uploaded file is not a readable PDF.",
            ) from exc

    @staticmethod
    def document_response(record: DocumentRecord) -> DocumentResponse:
        return DocumentResponse(
            id=record.id,
            filename=record.original_filename,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            page_count=record.page_count,
            created_at=record.created_at,
            expires_at=record.expires_at,
            content_url=f"/api/v1/documents/{record.id}/content",
        )

    def get_document(self, document_id: str) -> DocumentRecord:
        record = self.storage.get_document(document_id)
        if record is None:
            raise WebAPIError(
                status_code=404,
                code="DOCUMENT_NOT_FOUND",
                message="Document was not found.",
            )
        if record.expires_at <= utc_now():
            self._delete_document_files(record.id)
            raise WebAPIError(
                status_code=410,
                code="DOCUMENT_EXPIRED",
                message="Document has expired and was removed.",
            )
        return record

    def document_path(self, document_id: str) -> tuple[DocumentRecord, Path]:
        record = self.get_document(document_id)
        path = self.storage.document_pdf(document_id)
        if not path.exists():
            raise WebAPIError(
                status_code=500,
                code="STORAGE_FAILED",
                message="The document file is unavailable.",
                retryable=True,
            )
        return record, path

    def create_job(self, document_id: str, converter_id: str) -> JobResponse:
        document = self.get_document(document_id)
        converter = self.registry.get(converter_id)
        if converter is None:
            raise WebAPIError(
                status_code=404,
                code="CONVERTER_NOT_FOUND",
                message="Converter was not found.",
            )
        if not converter.available:
            raise WebAPIError(
                status_code=409,
                code="CONVERTER_UNAVAILABLE",
                message=converter.unavailable_reason or "Converter is unavailable.",
            )

        job_id = str(uuid.uuid4())
        now = utc_now()
        self.storage.create_job_directory(job_id)
        job = JobRecord(
            id=job_id,
            document_id=document.id,
            converter_id=converter_id,
            status=JobStatus.QUEUED,
            stage="queued",
            created_at=now,
            queued_at=now,
            expires_at=document.expires_at,
            adapter_output_path="adapter",
        )
        self.storage.save_job(job)
        self._submit(job_id)
        logger.info(
            "conversion queued",
            extra={"job_id": job_id, "document_id": document_id, "converter_id": converter_id},
        )
        return self.job_response(job)

    def _submit(self, job_id: str) -> None:
        if self._stopped:
            raise WebAPIError(
                status_code=503,
                code="EXECUTOR_STOPPED",
                message="Conversion executor is stopped.",
                retryable=True,
            )
        future = self._executor.submit(self._execute_job, job_id)
        with self._future_lock:
            self._futures[job_id] = future
        future.add_done_callback(lambda _: self._forget_future(job_id))

    def _forget_future(self, job_id: str) -> None:
        with self._future_lock:
            self._futures.pop(job_id, None)

    def _set_job_state(self, job_id: str, status: JobStatus, stage: str) -> JobRecord:
        def update(job: JobRecord) -> None:
            job.status = status
            job.stage = stage
            if status == JobStatus.PROCESSING and job.started_at is None:
                job.started_at = utc_now()
                job.attempt += 1

        return self.storage.update_job(job_id, update)[0]

    def _execute_job(self, job_id: str) -> None:
        job = self.storage.get_job(job_id)
        if job is None or job.status != JobStatus.QUEUED:
            return
        try:
            self._set_job_state(job_id, JobStatus.PROCESSING, "processing")
            document, pdf_path = self.document_path(job.document_id)
            output_dir = self.storage.job_dir(job_id) / "adapter"
            result = self.runner.run(
                converter_id=job.converter_id,
                document_id=job.document_id,
                pdf_path=pdf_path,
                output_dir=output_dir,
            )
            self._set_job_state(job_id, JobStatus.EXPORTING, "exporting")

            export_dir = self.storage.job_dir(job_id) / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            json_path = export_dir / "result.json"
            text_path = export_dir / "result.txt"
            public_payload = public_document_payload(
                result.document, original_filename=document.original_filename
            )
            json_path.write_text(
                json.dumps(public_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            text_path.write_text(render_text(result.document), encoding="utf-8")
            finished = utc_now()

            def succeed(current: JobRecord) -> None:
                current.status = JobStatus.SUCCEEDED
                current.stage = "succeeded"
                current.finished_at = finished
                current.processing_time_seconds = result.processing_time_seconds
                current.warnings = result.warnings
                current.result_json_path = "exports/result.json"
                current.text_path = "exports/result.txt"

            self.storage.update_job(job_id, succeed)
            logger.info(
                "conversion succeeded",
                extra={
                    "job_id": job_id,
                    "document_id": job.document_id,
                    "converter_id": job.converter_id,
                    "duration_seconds": result.processing_time_seconds,
                },
            )
        except ConversionExecutionError as exc:
            self._fail_job(job_id, exc.code, exc.message, exc.retryable)
        except WebAPIError as exc:
            self._fail_job(job_id, exc.code, exc.message, exc.retryable)
        except Exception:
            logger.exception(
                "unexpected conversion failure",
                extra={"job_id": job_id, "converter_id": job.converter_id},
            )
            self._fail_job(
                job_id,
                "INTERNAL_CONVERSION_ERROR",
                "An unexpected error occurred while processing the PDF.",
                False,
            )

    def _fail_job(self, job_id: str, code: str, message: str, retryable: bool) -> None:
        finished = utc_now()

        def fail(job: JobRecord) -> None:
            job.status = JobStatus.FAILED
            job.stage = "failed"
            job.finished_at = finished
            if job.started_at:
                job.processing_time_seconds = max(
                    0.0, (finished - job.started_at).total_seconds()
                )
            job.error_code = code
            job.error_message = message
            job.error_retryable = retryable

        try:
            job = self.storage.update_job(job_id, fail)[0]
        except KeyError:
            return
        logger.error(
            "conversion failed",
            extra={
                "job_id": job_id,
                "document_id": job.document_id,
                "converter_id": job.converter_id,
                "error_code": code,
                "retryable": retryable,
            },
        )

    def get_job(self, job_id: str) -> JobRecord:
        record = self.storage.get_job(job_id)
        if record is None:
            raise WebAPIError(
                status_code=404,
                code="JOB_NOT_FOUND",
                message="Conversion job was not found.",
            )
        return record

    @staticmethod
    def job_response(record: JobRecord) -> JobResponse:
        succeeded = record.status == JobStatus.SUCCEEDED
        error = None
        if record.error_code:
            error = ErrorDetail(
                code=record.error_code,
                message=record.error_message or "Conversion failed.",
                retryable=record.error_retryable,
            )
        return JobResponse(
            id=record.id,
            document_id=record.document_id,
            converter_id=record.converter_id,
            status=record.status,
            stage=record.stage,
            created_at=record.created_at,
            queued_at=record.queued_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            processing_time_seconds=record.processing_time_seconds,
            warnings=record.warnings,
            error=error,
            result_url=f"/api/v1/jobs/{record.id}/result" if succeeded else None,
            text_download_url=(
                f"/api/v1/jobs/{record.id}/downloads/text" if succeeded else None
            ),
            json_download_url=(
                f"/api/v1/jobs/{record.id}/downloads/json" if succeeded else None
            ),
        )

    def _completed_artifact(self, job_id: str, relative: str | None) -> tuple[JobRecord, Path]:
        job = self.get_job(job_id)
        if job.status != JobStatus.SUCCEEDED or not relative:
            raise WebAPIError(
                status_code=409,
                code="RESULT_NOT_READY",
                message="Conversion result is not ready.",
                retryable=job.status not in TERMINAL_JOB_STATUSES,
            )
        base = self.storage.job_dir(job_id).resolve()
        path = (base / relative).resolve()
        if base not in path.parents or not path.is_file():
            raise WebAPIError(
                status_code=500,
                code="STORAGE_FAILED",
                message="Result artifact is unavailable.",
                retryable=True,
            )
        return job, path

    def get_result(self, job_id: str) -> ResultResponse:
        job, path = self._completed_artifact(
            job_id, self.get_job(job_id).result_json_path
        )
        try:
            document = StandardizedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WebAPIError(
                status_code=500,
                code="STORAGE_FAILED",
                message="Result JSON is invalid.",
                retryable=True,
            ) from exc
        return ResultResponse(
            job_id=job.id,
            converter_id=job.converter_id,
            processing_time_seconds=job.processing_time_seconds or 0.0,
            document=document,
        )

    def text_download(self, job_id: str) -> tuple[JobRecord, Path]:
        job = self.get_job(job_id)
        return self._completed_artifact(job_id, job.text_path)

    def json_download(self, job_id: str) -> tuple[JobRecord, Path]:
        job = self.get_job(job_id)
        return self._completed_artifact(job_id, job.result_json_path)

    def asset_download(self, job_id: str, asset_path: str) -> tuple[JobRecord, Path]:
        """Return an extracted visual asset without permitting path traversal."""
        job = self.get_job(job_id)
        if job.status != JobStatus.SUCCEEDED:
            raise WebAPIError(
                status_code=409,
                code="RESULT_NOT_READY",
                message="Conversion result is not ready.",
                retryable=job.status not in TERMINAL_JOB_STATUSES,
            )
        normalized = asset_path.replace("\\", "/").strip("/")
        parts = [part for part in normalized.split("/") if part]
        if parts and parts[0] == "assets":
            parts.pop(0)
        if not parts or any(part in {".", ".."} for part in parts):
            raise WebAPIError(
                status_code=404,
                code="ASSET_NOT_FOUND",
                message="Extracted asset was not found.",
            )
        assets_root = (self.storage.job_dir(job_id) / "adapter" / "assets").resolve()
        candidate = assets_root.joinpath(*parts).resolve()
        if assets_root not in candidate.parents or not candidate.is_file():
            raise WebAPIError(
                status_code=404,
                code="ASSET_NOT_FOUND",
                message="Extracted asset was not found.",
            )
        return job, candidate

    def delete_document(self, document_id: str) -> None:
        self.get_document(document_id)
        active = [
            job
            for job in self.storage.iter_jobs()
            if job.document_id == document_id and job.status not in TERMINAL_JOB_STATUSES
        ]
        if active:
            raise WebAPIError(
                status_code=409,
                code="DOCUMENT_IN_USE",
                message="Document has an active conversion job.",
            )
        self._delete_document_files(document_id)

    def _delete_document_files(self, document_id: str) -> None:
        self.storage.remove_jobs_for_document(document_id)
        self.storage.delete_document(document_id)
        logger.info("document deleted", extra={"document_id": document_id})

    def cleanup_expired(self) -> dict[str, int]:
        now = utc_now()
        removed_documents = 0
        skipped_active = 0
        jobs = self.storage.iter_jobs()
        active_document_ids = {
            job.document_id for job in jobs if job.status not in TERMINAL_JOB_STATUSES
        }
        for document in self.storage.iter_documents():
            if document.expires_at > now:
                continue
            if document.id in active_document_ids:
                skipped_active += 1
                continue
            self._delete_document_files(document.id)
            removed_documents += 1
        return {"removed_documents": removed_documents, "skipped_active": skipped_active}

    def recover_jobs(self) -> None:
        for job in self.storage.iter_jobs():
            if job.status == JobStatus.QUEUED:
                self._submit(job.id)
            elif job.status not in TERMINAL_JOB_STATUSES:
                self._fail_job(
                    job.id,
                    "SERVER_RESTARTED",
                    "Conversion was interrupted by a server restart.",
                    True,
                )

    def shutdown(self) -> None:
        self._stopped = True
        shutdown_runner = getattr(self.runner, "shutdown", None)
        if callable(shutdown_runner):
            shutdown_runner()
        self._executor.shutdown(wait=True, cancel_futures=True)
