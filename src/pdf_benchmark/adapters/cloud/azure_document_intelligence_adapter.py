from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import ToolExecutionError, ToolOutputParseError
from pdf_benchmark.adapters.cloud.base import BaseCloudAdapter
from pdf_benchmark.adapters.cloud.helpers import bbox_from_polygon, pdf_page_count, split_pdf
from pdf_benchmark.models import Caption, Formula, ImageObject, Page, RawToolResult, StandardizedDocument, Table, TableCell, TextBlock
from pdf_benchmark.utils.io import ensure_dir, read_json, write_json


class AzureDocumentIntelligenceAdapter(BaseCloudAdapter):
    tool_name = "azure_document_intelligence"
    distribution_name = "azure-ai-documentintelligence"
    pinned_version = "1.0.2"
    required_env = ("DOCUMENTINTELLIGENCE_ENDPOINT", "DOCUMENTINTELLIGENCE_API_KEY")

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status in {408, 409, 425, 429, 500, 502, 503, 504}:
            return True
        return exc.__class__.__name__ in {"ServiceRequestError", "ServiceResponseError"}

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        if self.mock_mode:
            return self.load_mock_json(raw_dir)
        self.require_credentials()
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceClient
            from azure.core.credentials import AzureKeyCredential
        except Exception as exc:
            raise ToolExecutionError("azure-ai-documentintelligence is not installed") from exc

        ensure_dir(raw_dir)
        response_dir = ensure_dir(raw_dir / "responses")
        chunk_dir = ensure_dir(raw_dir / "chunks")
        pages = pdf_page_count(pdf_path)
        tier = str(self.config.get("tier", "F0")).upper()
        chunk_size = int(self.config.get("pages_per_request", 2 if tier == "F0" else min(2000, pages)))
        chunks = split_pdf(pdf_path, chunk_dir, chunk_size)
        client = DocumentIntelligenceClient(
            endpoint=str(self.env("DOCUMENTINTELLIGENCE_ENDPOINT")),
            credential=AzureKeyCredential(str(self.env("DOCUMENTINTELLIGENCE_API_KEY"))),
            retry_total=max(0, int(self.config.get("max_attempts", 6)) - 1),
            retry_backoff_factor=float(self.config.get("retry_base_seconds", 1.0)),
        )
        model_id = str(self.config.get("model_id", "prebuilt-layout"))
        min_interval = float(self.config.get("min_request_interval_seconds", 1.05 if tier == "F0" else 0.0))
        timeout = float(self.config.get("timeout_seconds", 600))
        started = time.monotonic()
        artifacts: list[str] = []
        chunk_metrics = []
        last_start = 0.0

        for idx, (chunk_path, page_offset, count) in enumerate(chunks, start=1):
            if tier == "F0" and chunk_path.stat().st_size > 4 * 1024 * 1024:
                raise ToolExecutionError(
                    f"Azure F0 chunk {chunk_path.name} is >4 MB. Reduce pages_per_request or use S0."
                )
            elapsed_since = time.monotonic() - last_start
            if last_start and elapsed_since < min_interval:
                time.sleep(min_interval - elapsed_since)

            chunk_started = time.monotonic()

            def analyze():
                nonlocal last_start
                last_start = time.monotonic()
                with chunk_path.open("rb") as f:
                    poller = client.begin_analyze_document(
                        model_id,
                        body=f,
                        output_content_format=str(self.config.get("output_content_format", "markdown")),
                    )
                    result = poller.result(timeout=timeout)
                return result

            # Azure SDK retry policy handles transient transport/throttling errors
            # without our code replaying a completed billable analysis blindly.
            result = analyze()
            result_dict = result.as_dict() if hasattr(result, "as_dict") else dict(result)
            target = response_dir / f"chunk_{idx:04d}.json"
            write_json(target, {"page_offset": page_offset, "page_count": count, "result": result_dict})
            artifacts.append(str(target))
            chunk_metrics.append({"chunk": idx, "page_offset": page_offset, "pages": count, "seconds": time.monotonic() - chunk_started})

        latency = time.monotonic() - started
        manifest = {"responses": artifacts, "chunks": chunk_metrics, "tier": tier, "model_id": model_id}
        write_json(raw_dir / "manifest.json", manifest)
        artifacts.append(str(raw_dir / "manifest.json"))
        return RawToolResult(
            primary_artifact=str(raw_dir / "manifest.json"), artifacts=artifacts,
            metadata={
                "mock": False, "pages": pages, "requests": len(chunks), "tier": tier,
                "latency_seconds": latency, "processing_seconds": latency,
                "estimated_cost_usd": 0.0 if tier == "F0" and bool(self.config.get("assume_free_quota_available", True)) else None,
                "actual_cost_usd": None,
                "pricing_basis": "F0 processes only first 2 PDF/TIFF pages per request; benchmark splits into <=2-page chunks. F0 monthly quota availability is account-dependent.",
            },
        )

    @staticmethod
    def _regions(obj: dict[str, Any]) -> list[dict[str, Any]]:
        return obj.get("bounding_regions") or obj.get("boundingRegions") or []

    def _parse_result(self, wrapper: dict[str, Any], pages_by_num: dict[int, Page], order: int) -> int:
        offset = int(wrapper.get("page_offset", 0) or 0)
        data = wrapper.get("result", wrapper)
        local_to_global: dict[int, int] = {}
        for idx, p in enumerate(data.get("pages") or [], start=1):
            local = int(p.get("page_number", p.get("pageNumber", idx)) or idx)
            global_no = offset + local
            local_to_global[local] = global_no
            width = float(p.get("width", 1.0) or 1.0); height = float(p.get("height", 1.0) or 1.0)
            page = pages_by_num.setdefault(global_no, Page(page_number=global_no, width=width, height=height))
            for line in p.get("lines") or []:
                eid = f"azure_p{global_no}_{order:06d}"
                page.text_blocks.append(TextBlock(
                    element_id=eid, page_number=global_no,
                    bbox=bbox_from_polygon(line.get("polygon"), width, height),
                    raw_text=str(line.get("content") or ""), block_type="paragraph",
                    order_index=order, provenance={"source": "pages.lines"},
                )); page.reading_order.append(eid); order += 1
            for formula in p.get("formulas") or []:
                eid = f"azure_p{global_no}_{order:06d}"
                page.formulas.append(Formula(
                    element_id=eid, page_number=global_no,
                    bbox=bbox_from_polygon(formula.get("polygon"), width, height),
                    raw_text=str(formula.get("value") or formula.get("content") or ""),
                    latex=str(formula.get("value") or formula.get("content") or "") or None,
                    formula_type=str(formula.get("kind") or "unknown") if str(formula.get("kind") or "unknown") in {"inline","display","unknown"} else "unknown",
                    confidence=formula.get("confidence"), order_index=order,
                    provenance={"source": "pages.formulas"},
                )); page.reading_order.append(eid); order += 1

        # Paragraphs carry semantic roles but duplicate line text. Keep them only when role is useful.
        role_map = {"title":"heading", "sectionHeading":"heading", "pageHeader":"header", "pageFooter":"footer", "footnote":"footer"}
        for para in data.get("paragraphs") or []:
            role = para.get("role")
            if not role:
                continue
            regions = self._regions(para)
            if not regions:
                continue
            local = int(regions[0].get("page_number", regions[0].get("pageNumber", 1)) or 1)
            global_no = offset + local
            page = pages_by_num.get(global_no)
            if not page:
                continue
            eid = f"azure_p{global_no}_{order:06d}"
            page.text_blocks.append(TextBlock(
                element_id=eid, page_number=global_no,
                bbox=bbox_from_polygon(regions[0].get("polygon"), page.width, page.height),
                raw_text=str(para.get("content") or ""), block_type=role_map.get(str(role), "paragraph"),
                order_index=order, provenance={"source":"paragraphs", "role":role},
            )); page.reading_order.append(eid); order += 1

        for table in data.get("tables") or []:
            regions = self._regions(table)
            local = int((regions[0] if regions else {}).get("page_number", (regions[0] if regions else {}).get("pageNumber", 1)) or 1)
            global_no = offset + local; page = pages_by_num.get(global_no)
            if not page: continue
            cells=[]
            for c in table.get("cells") or []:
                cells.append(TableCell(
                    row_index=int(c.get("row_index", c.get("rowIndex", 0)) or 0),
                    column_index=int(c.get("column_index", c.get("columnIndex", 0)) or 0),
                    row_span=int(c.get("row_span", c.get("rowSpan", 1)) or 1),
                    column_span=int(c.get("column_span", c.get("columnSpan", 1)) or 1),
                    text=str(c.get("content") or ""),
                    is_header=str(c.get("kind") or "").lower() in {"columnheader", "rowheader", "stubhead"},
                ))
            eid=f"azure_p{global_no}_{order:06d}"
            page.tables.append(Table(
                element_id=eid, page_number=global_no,
                bbox=bbox_from_polygon((regions[0] if regions else {}).get("polygon"), page.width, page.height),
                rows=int(table.get("row_count", table.get("rowCount", 0)) or 0),
                columns=int(table.get("column_count", table.get("columnCount", 0)) or 0),
                cells=cells, order_index=order, provenance={"source":"tables"},
            )); page.reading_order.append(eid); order += 1

        for fig in data.get("figures") or []:
            regions=self._regions(fig)
            if not regions: continue
            local=int(regions[0].get("page_number", regions[0].get("pageNumber", 1)) or 1)
            global_no=offset+local; page=pages_by_num.get(global_no)
            if not page: continue
            cap=fig.get("caption") or {}
            eid=f"azure_p{global_no}_{order:06d}"
            page.images.append(ImageObject(
                element_id=eid, page_number=global_no,
                bbox=bbox_from_polygon(regions[0].get("polygon"), page.width, page.height),
                caption=Caption(text=str(cap.get("content") or "")) if cap.get("content") else None,
                extraction_success=False, order_index=order, provenance={"source":"figures"},
            )); page.reading_order.append(eid); order += 1
        return order

    def standardize(self, raw_result, raw_dir, assets_dir, *, document_id, pdf_path):
        pages_by_num: dict[int, Page] = {}; order=0
        if raw_result.metadata.get("mock"):
            wrappers=[read_json(raw_dir / "mock_response.json")]
        else:
            manifest=read_json(Path(raw_result.primary_artifact or ""))
            wrappers=[read_json(Path(p)) for p in manifest.get("responses") or []]
        for wrapper in wrappers:
            order=self._parse_result(wrapper, pages_by_num, order)
        if not pages_by_num:
            raise ToolOutputParseError("Azure response contained no pages")
        return StandardizedDocument(
            document_id=document_id, source_pdf=str(pdf_path), tool=self.tool_metadata(),
            pages=[pages_by_num[k] for k in sorted(pages_by_num)],
            metadata={"cloud_execution": raw_result.metadata},
        )
