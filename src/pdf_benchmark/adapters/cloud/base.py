from __future__ import annotations

import json
import os
import random
import shutil
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from pdf_benchmark.adapters.base import BaseLocalAdapter, ToolExecutionError
from pdf_benchmark.models import RawToolResult
from pdf_benchmark.utils.io import ensure_dir

T = TypeVar("T")


class BaseCloudAdapter(BaseLocalAdapter):
    """Common cloud adapter behavior.

    Cloud adapters retain the Prompt-7 two-stage contract:
      run_raw(pdf, raw_dir) -> persisted vendor response
      standardize(raw_result, ...) -> StandardizedDocument

    `mock=True` guarantees that no network request is made.
    """

    required_env: tuple[str, ...] = ()

    def env(self, name: str, *, required: bool = True, default: str | None = None) -> str | None:
        value = os.getenv(name, default)
        if required and not value:
            raise ToolExecutionError(
                f"Missing environment variable {name}. "
                "Copy .env.example values into your shell/secret store; never commit credentials."
            )
        return value

    @property
    def mock_mode(self) -> bool:
        return bool(self.config.get("mock", False))

    def require_credentials(self) -> None:
        if self.mock_mode:
            return
        missing = [name for name in self.required_env if not os.getenv(name)]
        if missing:
            raise ToolExecutionError("Missing credentials/environment variables: " + ", ".join(missing))

    def load_mock_json(self, raw_dir: Path) -> RawToolResult:
        fixture = self.config.get("mock_fixture")
        if not fixture:
            raise ToolExecutionError("mock=true requires config.mock_fixture")
        source = Path(fixture).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        ensure_dir(raw_dir)
        target = raw_dir / "mock_response.json"
        shutil.copy2(source, target)
        return RawToolResult(
            primary_artifact=str(target),
            artifacts=[str(target)],
            metadata={"mock": True, "network_calls": 0, "estimated_cost_usd": 0.0},
        )

    def retry_call(
        self,
        fn: Callable[[], T],
        *,
        is_retryable: Callable[[Exception], bool] | None = None,
        operation: str = "api_call",
    ) -> T:
        attempts = int(self.config.get("max_attempts", 5))
        base = float(self.config.get("retry_base_seconds", 1.0))
        cap = float(self.config.get("retry_max_seconds", 30.0))
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except Exception as exc:  # vendor SDK errors vary
                last = exc
                retryable = True if is_retryable is None else bool(is_retryable(exc))
                if not retryable or attempt >= attempts:
                    raise
                delay = min(cap, base * (2 ** (attempt - 1))) + random.uniform(0, min(0.25, base))
                self.logger.warning(
                    "%s failed (attempt %s/%s): %s; retrying in %.2fs",
                    operation,
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
        assert last is not None
        raise last

    def poll_until(
        self,
        fetch: Callable[[], dict[str, Any]],
        *,
        done: Callable[[dict[str, Any]], bool],
        failed: Callable[[dict[str, Any]], bool],
        description: str,
    ) -> dict[str, Any]:
        timeout = float(self.config.get("timeout_seconds", 900))
        interval = float(self.config.get("poll_interval_seconds", 2.0))
        started = time.monotonic()
        while True:
            payload = fetch()
            if failed(payload):
                raise ToolExecutionError(f"{description} failed: {json.dumps(payload, ensure_ascii=False)[:4000]}")
            if done(payload):
                return payload
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"Timed out waiting for {description} after {timeout:.0f}s")
            time.sleep(interval)
