from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _storage_root() -> Path:
    return Path(tempfile.gettempdir()) / "pdf-benchmark-web"


class WebSettings(BaseSettings):
    """Server-side configuration. Provider credentials are intentionally absent."""

    model_config = SettingsConfigDict(
        env_prefix="PDF_BENCHMARK_WEB_",
        env_file=None,
        extra="ignore",
    )

    project_root: Path = Field(default_factory=_project_root)
    storage_root: Path = Field(default_factory=_storage_root)
    max_upload_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    max_pages: int = Field(default=300, ge=1)
    document_ttl_seconds: int = Field(default=24 * 60 * 60, ge=60)
    cleanup_interval_seconds: int = Field(default=15 * 60, ge=10)
    upload_chunk_bytes: int = Field(default=1024 * 1024, ge=4096)
    executor_workers: int = Field(default=2, ge=1, le=32)
    adapter_timeout_seconds: int = Field(default=90 * 60, ge=10)
    allow_cloud: bool = False
    seed: int = 42
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    docs_enabled: bool = True

    def prepare(self) -> "WebSettings":
        self.project_root = self.project_root.resolve()
        self.storage_root = self.storage_root.resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        return self
