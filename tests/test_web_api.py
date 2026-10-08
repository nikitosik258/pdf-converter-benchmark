from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from pdf_benchmark.models import ImageObject, Page, StandardizedDocument, TextBlock, ToolMetadata
from pdf_benchmark.web.app import create_app
from pdf_benchmark.web.config import WebSettings
from pdf_benchmark.web.errors import ConversionExecutionError
from pdf_benchmark.web.runner import RunnerResult
from pdf_benchmark.web.service import WebService, safe_filename
from pdf_benchmark.web.storage import utc_now


class FakeRunner:
    def run(self, *, converter_id, document_id, pdf_path, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(exist_ok=True)
        (assets_dir / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        document = StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path.resolve()),
            tool=ToolMetadata(
                tool_name=converter_id,
                distribution_name="fake",
                version="1.0",
                configuration={"api_key": "must-not-leak"},
            ),
            pages=[
                Page(
                    page_number=1,
                    width=595,
                    height=842,
                    text_blocks=[
                        TextBlock(
                            element_id="text-1",
                            page_number=1,
                            raw_text="Benchmark PDF",
                            order_index=0,
                            provenance={"source_path": str(pdf_path.resolve())},
                        )
                    ],
                    images=[
                        ImageObject(
                            element_id="image-1",
                            page_number=1,
                            asset_path="assets/figure.png",
                            extraction_success=True,
                        )
                    ],
                    reading_order=["text-1"],
                )
            ],
            raw_artifacts=[str((output_dir / "raw" / "response.json").resolve())],
            metadata={"source_pdf": str(pdf_path.resolve()), "token": "secret"},
        )
        return RunnerResult(
            document=document,
            warnings=["fixture warning"],
            processing_time_seconds=0.125,
        )


class FailingRunner:
    def run(self, **_):
        raise ConversionExecutionError(
            "PARSER_TIMEOUT", "The converter exceeded its processing time limit."
        )


def _settings(tmp_path: Path, **overrides) -> WebSettings:
    values = {
        "project_root": Path(__file__).resolve().parents[1],
        "storage_root": tmp_path / "web-runtime",
        "max_upload_bytes": 1024 * 1024,
        "max_pages": 10,
        "document_ttl_seconds": 3600,
        "cleanup_interval_seconds": 3600,
        "executor_workers": 1,
        "docs_enabled": False,
        "allow_cloud": False,
    }
    values.update(overrides)
    return WebSettings(**values)


def _client(tmp_path: Path, runner=None, **settings_overrides):
    settings = _settings(tmp_path, **settings_overrides)
    service = WebService(settings, runner=runner or FakeRunner())
    return TestClient(create_app(settings, service=service)), service


def _upload(client: TestClient, pdf: Path, filename: str = "paper.pdf") -> dict:
    response = client.post(
        "/api/v1/documents",
        files={"file": (filename, pdf.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for_terminal(client: TestClient, job_id: str, *, timeout_seconds: float = 10) -> dict:
    for _ in range(int(timeout_seconds / 0.02)):
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("job did not reach a terminal state")


def test_complete_api_flow_and_safe_public_result(tmp_path: Path, minimal_pdf: Path):
    client, _ = _client(tmp_path)
    with client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["converters_total"] == 10

        converters = client.get("/api/v1/converters")
        assert converters.status_code == 200
        assert [item["id"] for item in converters.json()] == [
            "pymupdf",
            "pdfplumber",
            "pdfminer",
            "docling",
            "mineru",
            "ocr_space",
            "nutrient",
            "mindee",
            "adobe_extract",
            "llamaparse",
        ]
        assert all("API_KEY" not in json.dumps(item) for item in converters.json())
        assert all(not item["available"] for item in converters.json() if item["deployment"] == "cloud")

        uploaded = _upload(client, minimal_pdf, "../../CON?.pdf")
        assert uploaded["filename"] == "CON_.pdf"
        assert uploaded["page_count"] == 1
        assert uploaded["size_bytes"] == minimal_pdf.stat().st_size

        source = client.get(uploaded["content_url"])
        assert source.status_code == 200
        assert source.content == minimal_pdf.read_bytes()
        assert source.headers["content-type"] == "application/pdf"

        started = client.post(
            "/api/v1/jobs",
            json={"document_id": uploaded["id"], "converter_id": "pymupdf"},
        )
        assert started.status_code == 202, started.text
        completed = _wait_for_terminal(client, started.json()["id"])
        assert completed["status"] == "succeeded"
        assert completed["processing_time_seconds"] == 0.125
        assert completed["warnings"] == ["fixture warning"]

        result = client.get(completed["result_url"])
        assert result.status_code == 200
        document = result.json()["document"]
        assert document["source_pdf"] == "CON_.pdf"
        assert document["raw_artifacts"] == []
        assert "token" not in document["metadata"]
        assert document["pages"][0]["text_blocks"][0]["provenance"]["source_path"] == "source.pdf"

        text_download = client.get(completed["text_download_url"])
        assert text_download.status_code == 200
        assert "--- Page 1 ---" in text_download.text
        assert "Benchmark PDF" in text_download.text

        json_download = client.get(completed["json_download_url"])
        assert json_download.status_code == 200
        assert StandardizedDocument.model_validate(json_download.json()) == StandardizedDocument.model_validate(document)

        asset = client.get(f"/api/v1/jobs/{completed['id']}/assets/assets/figure.png")
        assert asset.status_code == 200
        assert asset.content.startswith(b"\x89PNG")
        traversal = client.get(f"/api/v1/jobs/{completed['id']}/assets/../job.json")
        assert traversal.status_code == 404


def test_upload_rejects_invalid_and_oversized_files(tmp_path: Path):
    client, _ = _client(tmp_path, max_upload_bytes=1024)
    with client:
        invalid = client.post(
            "/api/v1/documents",
            files={"file": ("bad.pdf", b"not a pdf", "application/pdf")},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "PDF_INVALID"
        assert invalid.headers["x-request-id"]

        oversized = client.post(
            "/api/v1/documents",
            files={"file": ("large.pdf", b"%PDF-" + b"x" * 2048, "application/pdf")},
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "LIMIT_FILE_SIZE"


def test_request_validation_and_unavailable_converter(tmp_path: Path, minimal_pdf: Path):
    client, _ = _client(tmp_path)
    with client:
        invalid = client.post("/api/v1/jobs", json={"document_id": "bad", "converter_id": "x!"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"

        uploaded = _upload(client, minimal_pdf)
        missing = client.post(
            "/api/v1/jobs",
            json={"document_id": uploaded["id"], "converter_id": "unknown"},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "CONVERTER_NOT_FOUND"

        cloud = client.post(
            "/api/v1/jobs",
            json={"document_id": uploaded["id"], "converter_id": "ocr_space"},
        )
        assert cloud.status_code == 409
        assert cloud.json()["error"]["code"] == "CONVERTER_UNAVAILABLE"


def test_failed_job_exposes_stable_error(tmp_path: Path, minimal_pdf: Path):
    client, _ = _client(tmp_path, runner=FailingRunner())
    with client:
        uploaded = _upload(client, minimal_pdf)
        started = client.post(
            "/api/v1/jobs",
            json={"document_id": uploaded["id"], "converter_id": "pymupdf"},
        )
        completed = _wait_for_terminal(client, started.json()["id"])
        assert completed["status"] == "failed"
        assert completed["error"] == {
            "code": "PARSER_TIMEOUT",
            "message": "The converter exceeded its processing time limit.",
            "retryable": False,
            "request_id": None,
            "details": {},
        }
        unavailable = client.get(f"/api/v1/jobs/{completed['id']}/result")
        assert unavailable.status_code == 409
        assert unavailable.json()["error"]["code"] == "RESULT_NOT_READY"


def test_cleanup_and_delete_remove_isolated_runtime_files(tmp_path: Path, minimal_pdf: Path):
    client, service = _client(tmp_path)
    with client:
        first = _upload(client, minimal_pdf, "first.pdf")
        deleted = client.delete(f"/api/v1/documents/{first['id']}")
        assert deleted.status_code == 204
        assert service.storage.get_document(first["id"]) is None

        second = _upload(client, minimal_pdf, "second.pdf")
        record = service.storage.get_document(second["id"])
        assert record is not None
        record.expires_at = utc_now() - timedelta(seconds=1)
        service.storage.save_document(record)
        cleanup = service.cleanup_expired()
        assert cleanup == {"removed_documents": 1, "skipped_active": 0}
        assert service.storage.get_document(second["id"]) is None


def test_safe_filename_handles_paths_and_reserved_names():
    assert safe_filename(r"C:\temp\paper.pdf") == "paper.pdf"
    assert safe_filename("../../report") == "report.pdf"
    assert safe_filename("NUL.pdf") == "document_NUL.pdf"
    assert safe_filename("\x00\n.pdf") == "__.pdf"


def test_static_frontend_is_served(tmp_path: Path):
    client, _ = _client(tmp_path)
    with client:
        page = client.get("/")
        assert page.status_code == 200
        assert "PDF Converter Lab" in page.text
        script = client.get("/static/app.js")
        assert script.status_code == 200
        assert "uploadAndStart" in script.text
        assert "ui.workspace.hidden = true" in script.text
        assert "state.workspace.hidden" not in script.text


def test_real_pymupdf_adapter_is_reused_end_to_end(tmp_path: Path, minimal_pdf: Path):
    settings = _settings(tmp_path, adapter_timeout_seconds=60)
    service = WebService(settings)
    client = TestClient(create_app(settings, service=service))
    with client:
        uploaded = _upload(client, minimal_pdf, "native.pdf")
        started = client.post(
            "/api/v1/jobs",
            json={"document_id": uploaded["id"], "converter_id": "pymupdf"},
        )
        assert started.status_code == 202, started.text
        completed = _wait_for_terminal(client, started.json()["id"], timeout_seconds=60)
        assert completed["status"] == "succeeded", completed
        result = client.get(completed["result_url"])
        assert result.status_code == 200
        assert result.json()["document"]["tool"]["tool_name"] == "pymupdf"
        assert "Benchmark PDF" in client.get(completed["text_download_url"]).text
