from __future__ import annotations

import asyncio
import logging
import mimetypes
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, FastAPI, File, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import WebSettings
from .errors import WebAPIError
from .logging import configure_logging, request_id_var
from .schemas import (
    ConversionCreate,
    ConverterInfo,
    DocumentResponse,
    ErrorDetail,
    ErrorEnvelope,
    HealthResponse,
    JobResponse,
    ResultResponse,
)
from .service import WebService


logger = logging.getLogger("pdf_benchmark.web.api")
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict | None = None,
) -> JSONResponse:
    request_id = request_id_var.get()
    payload = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            request_id=request_id,
            details=details or {},
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def create_app(
    settings: WebSettings | None = None, *, service: WebService | None = None
) -> FastAPI:
    settings = (settings or WebSettings()).prepare()
    configure_logging(settings.log_level)
    web_service = service or WebService(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.web_service = web_service
        web_service.recover_jobs()

        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(settings.cleanup_interval_seconds)
                result = await asyncio.to_thread(web_service.cleanup_expired)
                if result["removed_documents"]:
                    logger.info("expired documents cleaned", extra=result)

        cleanup_task = asyncio.create_task(cleanup_loop(), name="web-runtime-cleanup")
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            web_service.shutdown()

    app = FastAPI(
        title="PDF Converter API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def frontend() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        if len(request_id) > 128 or any(ord(char) < 32 for char in request_id):
            request_id = str(uuid.uuid4())
        token = request_id_var.set(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "http request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(WebAPIError)
    async def web_error_handler(_: Request, exc: WebAPIError) -> JSONResponse:
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        safe_errors = [
            {"type": item.get("type"), "loc": item.get("loc"), "msg": item.get("msg")}
            for item in exc.errors()
        ]
        return _error_response(
            status_code=422,
            code="REQUEST_VALIDATION_FAILED",
            message="Request validation failed.",
            details={"errors": safe_errors},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error", exc_info=exc)
        return _error_response(
            status_code=500,
            code="INTERNAL_ERROR",
            message="An unexpected server error occurred.",
        )

    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        storage_ok = web_service.storage.healthcheck()
        converters = web_service.registry.list()
        executor_ok = web_service.executor_running
        return HealthResponse(
            status="ok" if storage_ok and executor_ok else "degraded",
            storage="ok" if storage_ok else "error",
            executor="ok" if executor_ok else "stopped",
            converters_available=sum(item.available for item in converters),
            converters_total=len(converters),
        )

    @router.get("/converters", response_model=list[ConverterInfo], tags=["converters"])
    def converters() -> list[ConverterInfo]:
        return web_service.registry.list()

    @router.post(
        "/documents",
        response_model=DocumentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["documents"],
    )
    async def upload_document(file: UploadFile = File(...)) -> DocumentResponse:
        return await web_service.upload_document(file)

    @router.get(
        "/documents/{document_id}",
        response_model=DocumentResponse,
        tags=["documents"],
    )
    def document(document_id: str) -> DocumentResponse:
        return web_service.document_response(web_service.get_document(document_id))

    @router.get("/documents/{document_id}/content", tags=["documents"])
    def document_content(document_id: str) -> FileResponse:
        record, path = web_service.document_path(document_id)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=record.original_filename,
            content_disposition_type="inline",
        )

    @router.delete(
        "/documents/{document_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["documents"],
    )
    def delete_document(document_id: str) -> Response:
        web_service.delete_document(document_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["jobs"],
    )
    def create_job(payload: ConversionCreate) -> JobResponse:
        return web_service.create_job(payload.document_id, payload.converter_id)

    @router.get("/jobs/{job_id}", response_model=JobResponse, tags=["jobs"])
    def job(job_id: str) -> JobResponse:
        return web_service.job_response(web_service.get_job(job_id))

    @router.get(
        "/jobs/{job_id}/result", response_model=ResultResponse, tags=["results"]
    )
    def result(job_id: str) -> ResultResponse:
        return web_service.get_result(job_id)

    @router.get("/jobs/{job_id}/downloads/text", tags=["results"])
    def download_text(job_id: str) -> FileResponse:
        job_record, path = web_service.text_download(job_id)
        document_record = web_service.get_document(job_record.document_id)
        base = document_record.original_filename.rsplit(".", 1)[0]
        return FileResponse(
            path,
            media_type="text/plain; charset=utf-8",
            filename=f"{base}-{job_record.converter_id}.txt",
        )

    @router.get("/jobs/{job_id}/downloads/json", tags=["results"])
    def download_json(job_id: str) -> FileResponse:
        job_record, path = web_service.json_download(job_id)
        document_record = web_service.get_document(job_record.document_id)
        base = document_record.original_filename.rsplit(".", 1)[0]
        return FileResponse(
            path,
            media_type="application/json",
            filename=f"{base}-{job_record.converter_id}.json",
        )

    @router.get("/jobs/{job_id}/assets/{asset_path:path}", tags=["results"])
    def asset(job_id: str, asset_path: str) -> FileResponse:
        _, path = web_service.asset_download(job_id, asset_path)
        media_type, _ = mimetypes.guess_type(path.name)
        return FileResponse(path, media_type=media_type or "application/octet-stream")

    app.include_router(router)
    return app


def run() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the PDF Converter FastAPI backend.")
    parser.add_argument("--host", help="Override PDF_BENCHMARK_WEB_HOST")
    parser.add_argument("--port", type=int, help="Override PDF_BENCHMARK_WEB_PORT")
    parser.add_argument("--log-level", help="Override PDF_BENCHMARK_WEB_LOG_LEVEL")
    args = parser.parse_args()
    overrides = {
        key: value
        for key, value in {
            "host": args.host,
            "port": args.port,
            "log_level": args.log_level,
        }.items()
        if value is not None
    }
    settings = WebSettings(**overrides).prepare()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
