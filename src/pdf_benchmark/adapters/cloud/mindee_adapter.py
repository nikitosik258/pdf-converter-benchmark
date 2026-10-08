from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import ToolExecutionError, ToolOutputParseError
from pdf_benchmark.adapters.cloud.base import BaseCloudAdapter
from pdf_benchmark.adapters.cloud.helpers import (
    bbox_from_normalized_polygon,
    pdf_page_count,
)
from pdf_benchmark.models import BBox, Page, RawToolResult, StandardizedDocument, TextBlock
from pdf_benchmark.utils.io import ensure_dir, read_json, write_json


def _union_bbox(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return BBox(
        x_min=min(b.x_min for b in boxes),
        y_min=min(b.y_min for b in boxes),
        x_max=max(b.x_max for b in boxes),
        y_max=max(b.y_max for b in boxes),
    )


def _load_mindee_v2_ocr_sdk():
    """Load the Mindee V2 OCR utility API.

    Mindee's V2 client lives under mindee.v2 in current SDKs, while input
    helpers such as FileInput / PollingOptions remain available from the
    top-level package. A fallback for Client is kept for compatible SDK builds.

    IMPORTANT:
    This adapter uses the dedicated V2 OCR utility product
    (/v2/products/ocr/...), not an Extraction Model with the optional
    `raw_text=True` feature.
    """
    try:
        from mindee import FileInput, PollingOptions
        try:
            from mindee.v2 import Client
        except ImportError:  # compatibility fallback
            from mindee import Client
        from mindee.v2.product.ocr import OCRParameters, OCRResponse
    except Exception as exc:
        raise ToolExecutionError(
            "Mindee V2 OCR SDK imports are unavailable. "
            "Expected mindee.v2.Client, mindee.FileInput, mindee.PollingOptions, "
            "mindee.v2.product.ocr.OCRParameters and OCRResponse."
        ) from exc

    return Client, FileInput, PollingOptions, OCRParameters, OCRResponse


class MindeeOCRAdapter(BaseCloudAdapter):
    tool_name = "mindee"
    distribution_name = "mindee"
    pinned_version = "5.3.1"
    required_env = ("MINDEE_V2_API_KEY", "MINDEE_OCR_MODEL_ID")

    def model_versions(self) -> dict[str, str]:
        return {
            "product": "Mindee V2 OCR utility / Raw Text OCR",
            "model_id": os.getenv("MINDEE_OCR_MODEL_ID", ""),
        }

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        if self.mock_mode:
            return self.load_mock_json(raw_dir)

        self.require_credentials()
        ensure_dir(raw_dir)

        Client, FileInput, PollingOptions, OCRParameters, OCRResponse = (
            _load_mindee_v2_ocr_sdk()
        )

        max_pages_per_request = int(
            self.config.get("max_pages_per_request", 10)
        )
        if max_pages_per_request <= 0:
            max_pages_per_request = 10

        os.environ.setdefault(
            "MINDEE_V2_REQUEST_TIMEOUT",
            str(int(self.config.get("request_timeout_seconds", 120))),
        )

        polling_options = None
        try:
            polling_options = PollingOptions(
                initial_delay_sec=float(
                    self.config.get("initial_poll_delay_seconds", 2.0)
                ),
                delay_sec=float(self.config.get("poll_interval_seconds", 1.5)),
                max_retries=int(self.config.get("poll_max_retries", 120)),
            )
        except Exception as exc:
            self.logger.warning(
                "Could not construct custom Mindee PollingOptions (%s); "
                "falling back to SDK defaults.",
                exc,
            )
            polling_options = None

        kwargs: dict[str, Any] = {
            "model_id": os.environ["MINDEE_OCR_MODEL_ID"],
        }
        if polling_options is not None:
            kwargs["polling_options"] = polling_options
        params = OCRParameters(**kwargs)

        try:
            import pymupdf
        except Exception as exc:
            raise ToolExecutionError(
                "PyMuPDF is required for Mindee trial chunking."
            ) from exc

        total_pages = pdf_page_count(pdf_path)
        if total_pages <= 0:
            raise ToolExecutionError(
                f"Could not determine page count for {pdf_path.name}"
            )

        chunk_raw_dir = ensure_dir(raw_dir / "chunks")
        client = Client(os.environ["MINDEE_V2_API_KEY"])
        started = time.monotonic()

        merged_pages: list[dict[str, Any]] = []
        first_payload: dict[str, Any] | None = None
        chunk_records: list[dict[str, Any]] = []
        provider_job_ids: list[str] = []

        def _provider_error(exc: Exception, start_page: int, end_page: int) -> None:
            status = (
                getattr(exc, "status_code", None)
                or getattr(exc, "status", None)
                or getattr(getattr(exc, "response", None), "status_code", None)
            )
            prefix = f"Mindee pages {start_page}-{end_page}: "
            if status == 401:
                raise ToolExecutionError(
                    prefix + "authentication failed (HTTP 401). Check MINDEE_V2_API_KEY."
                ) from exc
            if status in (400, 404, 422):
                raise ToolExecutionError(
                    prefix
                    + "request rejected. Check that MINDEE_OCR_MODEL_ID belongs "
                    "to a V2 OCR / Raw Text OCR utility model. "
                    f"Provider error: {exc}"
                ) from exc
            if status == 402:
                raise ToolExecutionError(
                    prefix
                    + "HTTP 402 plan/quota restriction. The adapter already "
                    f"chunks to <= {max_pages_per_request} pages per request, "
                    "so this is likely an account-wide quota/subscription limit."
                ) from exc
            if status == 429:
                raise ToolExecutionError(
                    prefix
                    + "rate limit reached (HTTP 429). The adapter intentionally "
                    "does not auto-resubmit ambiguous enqueue requests."
                ) from exc
            raise ToolExecutionError(
                prefix + f"OCR request failed: {exc}"
            ) from exc

        source_doc = None
        try:
            source_doc = pymupdf.open(pdf_path)

            for chunk_index, start0 in enumerate(
                range(0, total_pages, max_pages_per_request),
                start=1,
            ):
                end0 = min(start0 + max_pages_per_request, total_pages)
                start_page = start0 + 1
                end_page = end0

                chunk_pdf = (
                    chunk_raw_dir
                    / f"chunk_{chunk_index:03d}_p{start_page:04d}-{end_page:04d}.pdf"
                )

                chunk_doc = pymupdf.open()
                try:
                    chunk_doc.insert_pdf(
                        source_doc,
                        from_page=start0,
                        to_page=end0 - 1,
                    )
                    chunk_doc.save(chunk_pdf)
                finally:
                    chunk_doc.close()

                chunk_started = time.monotonic()
                try:
                    with chunk_pdf.open("rb") as fh:
                        input_source = FileInput(fh)
                        response = client.enqueue_and_get_result(
                            OCRResponse,
                            input_source,
                            params,
                        )
                except Exception as exc:
                    _provider_error(exc, start_page, end_page)

                chunk_latency = time.monotonic() - chunk_started

                raw_http = getattr(response, "raw_http", None)
                if raw_http:
                    payload = json.loads(raw_http)
                elif hasattr(response, "as_dict"):
                    payload = response.as_dict()
                else:
                    raise ToolOutputParseError(
                        "Mindee response has neither raw_http nor as_dict()"
                    )

                inference = payload.get("inference") or payload
                result = inference.get("result") or {}
                pages_payload = result.get("pages") or []
                if not pages_payload and isinstance(result.get("raw_text"), dict):
                    pages_payload = result["raw_text"].get("pages") or []

                expected_chunk_pages = end_page - start_page + 1
                if len(pages_payload) != expected_chunk_pages:
                    raise ToolOutputParseError(
                        "Mindee OCR chunk returned an unexpected number of pages: "
                        f"requested {start_page}-{end_page} "
                        f"({expected_chunk_pages}), received {len(pages_payload)}."
                    )

                chunk_response_path = (
                    chunk_raw_dir / f"chunk_{chunk_index:03d}_response.json"
                )
                write_json(chunk_response_path, payload)

                if first_payload is None:
                    first_payload = json.loads(json.dumps(payload))

                merged_pages.extend(pages_payload)

                job_obj = getattr(
                    getattr(response, "inference", None),
                    "job",
                    None,
                )
                job_id = getattr(job_obj, "id", None)
                if job_id:
                    provider_job_ids.append(str(job_id))

                chunk_records.append(
                    {
                        "chunk_index": chunk_index,
                        "start_page": start_page,
                        "end_page": end_page,
                        "page_count": expected_chunk_pages,
                        "latency_seconds": chunk_latency,
                        "response_artifact": str(chunk_response_path),
                        "provider_job_id": job_id,
                    }
                )

                try:
                    chunk_pdf.unlink()
                except OSError:
                    pass
        finally:
            if source_doc is not None:
                source_doc.close()
            try:
                client.close()
            except Exception:
                pass

        latency = time.monotonic() - started

        if len(merged_pages) != total_pages:
            raise ToolOutputParseError(
                "Mindee merged OCR output does not cover the full PDF: "
                f"expected {total_pages} pages, received {len(merged_pages)}."
            )

        if first_payload is None:
            raise ToolOutputParseError("Mindee produced no chunk responses.")

        if isinstance(first_payload.get("inference"), dict):
            merged_inference = first_payload["inference"]
        else:
            merged_inference = first_payload

        merged_result = merged_inference.get("result")
        if not isinstance(merged_result, dict):
            merged_result = {}
            merged_inference["result"] = merged_result

        merged_result["pages"] = merged_pages
        first_payload["_benchmark_chunking"] = {
            "enabled": total_pages > max_pages_per_request,
            "max_pages_per_request": max_pages_per_request,
            "original_page_count": total_pages,
            "chunk_count": len(chunk_records),
            "chunks": chunk_records,
        }

        target = raw_dir / "response.json"
        write_json(target, first_payload)

        assume_trial = bool(
            self.config.get("assume_free_trial_available", True)
        )

        artifacts = [str(target)]
        artifacts.extend(
            str(chunk_raw_dir / f"chunk_{i:03d}_response.json")
            for i in range(1, len(chunk_records) + 1)
        )

        return RawToolResult(
            primary_artifact=str(target),
            artifacts=artifacts,
            metadata={
                "mock": False,
                "pages": total_pages,
                "credits_estimate": total_pages,
                "latency_seconds": latency,
                "processing_seconds": latency,
                "estimated_cost_usd": (
                    0.0 if assume_trial and total_pages <= 200 else None
                ),
                "actual_cost_usd": None,
                "pricing_basis": (
                    "Mindee OCR is page/credit based; benchmark adapter "
                    "chunks trial requests to at most "
                    f"{max_pages_per_request} pages per request."
                ),
                "provider_job_id": (
                    provider_job_ids[0] if provider_job_ids else None
                ),
                "provider_job_ids": provider_job_ids,
                "chunk_count": len(chunk_records),
                "max_pages_per_request": max_pages_per_request,
            },
        )

    def standardize(
        self,
        raw_result,
        raw_dir,
        assets_dir,
        *,
        document_id,
        pdf_path,
    ):
        payload = read_json(
            raw_dir / "mock_response.json"
            if raw_result.metadata.get("mock")
            else Path(raw_result.primary_artifact or "")
        )

        inference = payload.get("inference") or payload
        result = inference.get("result") or {}

        # Dedicated OCR utility response.
        native_pages = result.get("pages") or []

        # Defensive fallback for a future response wrapper. This does NOT turn
        # an Extraction Model into the supported OCR utility path; it only
        # tolerates an extra raw_text container if Mindee changes serialization.
        if not native_pages and isinstance(result.get("raw_text"), dict):
            native_pages = result["raw_text"].get("pages") or []

        pages: list[Page] = []
        page_text: dict[str, str] = {}
        for idx, native in enumerate(native_pages, start=1):
            page = Page(page_number=idx, width=1.0, height=1.0)
            words = native.get("words") or []
            word_boxes: list[BBox] = []
            word_payloads = []

            for word_idx, word in enumerate(words):
                bbox = bbox_from_normalized_polygon(word.get("polygon"))
                if bbox:
                    word_boxes.append(bbox)
                word_payloads.append(
                    {
                        "content": str(
                            word.get("content")
                            or word.get("text")
                            or word.get("value")
                            or ""
                        ),
                        "polygon": word.get("polygon"),
                    }
                )
                text = word_payloads[-1]["content"]
                if text.strip():
                    eid = f"mindee_p{idx}_word_{word_idx:06d}"
                    page.text_blocks.append(TextBlock(
                        element_id=eid,
                        page_number=idx,
                        bbox=bbox,
                        raw_text=text,
                        order_index=word_idx,
                        provenance={
                            "source": "Mindee OCR pages.words",
                            "word_index": word_idx,
                            "word": word,
                            "coordinates": "Mindee normalized polygon coordinates",
                        },
                    ))
                    page.reading_order.append(eid)

            text = str(
                native.get("content")
                or native.get("text")
                or ""
            )
            # Keep the full page text for audit/export without a second scored
            # representation of all its words. Empty/legacy pages retain the
            # page-level fallback; missing word polygons never gain fake boxes.
            page_text[str(idx)] = text
            if not page.text_blocks:
                eid = f"mindee_p{idx}_000000"
                page.text_blocks.append(
                    TextBlock(
                        element_id=eid,
                        page_number=idx,
                        bbox=_union_bbox(word_boxes),
                        raw_text=text,
                        block_type="paragraph",
                        order_index=0,
                        provenance={
                            "source": "inference.result.pages",
                            "words": word_payloads,
                            "coordinates": "Mindee normalized polygon coordinates",
                        },
                    )
                )
                page.reading_order.append(eid)
            pages.append(page)

        if not pages:
            raise ToolOutputParseError(
                "Mindee OCR response contained no OCR pages."
            )

        return StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path),
            tool=self.tool_metadata(),
            pages=pages,
            metadata={"cloud_execution": raw_result.metadata, "mindee_page_text": page_text},
        )
