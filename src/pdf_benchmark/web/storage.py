from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from .schemas import DocumentRecord, JobRecord


T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validated_uuid(value: str) -> str:
    parsed = uuid.UUID(value)
    if str(parsed) != value.lower():
        raise ValueError("Identifier must be a canonical UUID")
    return str(parsed)


class RuntimeStorage:
    """Small persistent repository for the single-node web runtime.

    Files are isolated from benchmark caches. The interface is intentionally
    narrow so a PostgreSQL/S3 implementation can replace it later.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.documents_root = self.root / "documents"
        self.jobs_root = self.root / "jobs"
        self._lock = threading.RLock()
        self.documents_root.mkdir(parents=True, exist_ok=True)
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def healthcheck(self) -> bool:
        try:
            probe = self.root / ".healthcheck"
            probe.write_text("ok", encoding="ascii")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def document_dir(self, document_id: str) -> Path:
        return self.documents_root / _validated_uuid(document_id)

    def document_pdf(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "source.pdf"

    def document_record_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "document.json"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / _validated_uuid(job_id)

    def job_record_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def create_document_directory(self, document_id: str) -> Path:
        path = self.document_dir(document_id)
        path.mkdir(parents=True, exist_ok=False)
        return path

    def create_job_directory(self, job_id: str) -> Path:
        path = self.job_dir(job_id)
        path.mkdir(parents=True, exist_ok=False)
        return path

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def save_document(self, record: DocumentRecord) -> None:
        with self._lock:
            self._atomic_json(
                self.document_record_path(record.id), record.model_dump(mode="json")
            )

    def get_document(self, document_id: str) -> DocumentRecord | None:
        try:
            path = self.document_record_path(document_id)
        except ValueError:
            return None
        with self._lock:
            if not path.exists():
                return None
            return DocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save_job(self, record: JobRecord) -> None:
        with self._lock:
            self._atomic_json(self.job_record_path(record.id), record.model_dump(mode="json"))

    def get_job(self, job_id: str) -> JobRecord | None:
        try:
            path = self.job_record_path(job_id)
        except ValueError:
            return None
        with self._lock:
            if not path.exists():
                return None
            return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def update_job(self, job_id: str, update: Callable[[JobRecord], T]) -> tuple[JobRecord, T]:
        with self._lock:
            record = self.get_job(job_id)
            if record is None:
                raise KeyError(job_id)
            result = update(record)
            self.save_job(record)
            return record, result

    def iter_documents(self) -> list[DocumentRecord]:
        records: list[DocumentRecord] = []
        with self._lock:
            for path in self.documents_root.glob("*/document.json"):
                try:
                    records.append(DocumentRecord.model_validate_json(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return records

    def iter_jobs(self) -> list[JobRecord]:
        records: list[JobRecord] = []
        with self._lock:
            for path in self.jobs_root.glob("*/job.json"):
                try:
                    records.append(JobRecord.model_validate_json(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return records

    def _safe_rmtree(self, path: Path, expected_parent: Path) -> None:
        resolved = path.resolve()
        parent = expected_parent.resolve()
        if resolved.parent != parent:
            raise RuntimeError(f"Refusing to remove path outside runtime storage: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._safe_rmtree(self.job_dir(job_id), self.jobs_root)

    def delete_document(self, document_id: str) -> None:
        with self._lock:
            self._safe_rmtree(self.document_dir(document_id), self.documents_root)

    def remove_jobs_for_document(self, document_id: str) -> int:
        removed = 0
        for job in self.iter_jobs():
            if job.document_id == document_id:
                self.delete_job(job.id)
                removed += 1
        return removed
