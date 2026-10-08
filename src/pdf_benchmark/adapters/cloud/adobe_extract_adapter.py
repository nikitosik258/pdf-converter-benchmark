from __future__ import annotations

import csv
import io
import math
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import ToolExecutionError, ToolOutputParseError
from pdf_benchmark.adapters.cloud.base import BaseCloudAdapter
from pdf_benchmark.adapters.cloud.helpers import adobe_path_kind, pdf_page_count
from pdf_benchmark.models import Caption, ImageObject, Page, RawToolResult, StandardizedDocument, Table, TableCell, TextBlock
from pdf_benchmark.utils.geometry import normalize_bbox
from pdf_benchmark.utils.io import ensure_dir, read_json, write_json


class AdobeExtractAdapter(BaseCloudAdapter):
    tool_name = "adobe_extract"
    distribution_name = "pdfservices-sdk"
    pinned_version = "4.2.0"
    required_env = ("PDF_SERVICES_CLIENT_ID", "PDF_SERVICES_CLIENT_SECRET")

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        if self.mock_mode:
            return self.load_mock_json(raw_dir)
        self.require_credentials()
        import os

        try:
            from adobe.pdfservices.operation.auth.service_principal_credentials import ServicePrincipalCredentials
            from adobe.pdfservices.operation.io.cloud_asset import CloudAsset
            from adobe.pdfservices.operation.io.stream_asset import StreamAsset
            from adobe.pdfservices.operation.pdf_services import PDFServices
            from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType
            from adobe.pdfservices.operation.pdfjobs.jobs.extract_pdf_job import ExtractPDFJob
            from adobe.pdfservices.operation.pdfjobs.params.extract_pdf import extract_renditions_element_type
            from adobe.pdfservices.operation.pdfjobs.params.extract_pdf.extract_element_type import ExtractElementType
            from adobe.pdfservices.operation.pdfjobs.params.extract_pdf.extract_pdf_params import ExtractPDFParams
            from adobe.pdfservices.operation.pdfjobs.params.extract_pdf.table_structure_type import TableStructureType
            from adobe.pdfservices.operation.pdfjobs.result.extract_pdf_result import ExtractPDFResult
            ExtractRenditionsElementType = extract_renditions_element_type.ExtractRenditionsElementType
        except Exception as exc:
            raise ToolExecutionError("Adobe pdfservices-sdk is not installed or its API changed") from exc

        ensure_dir(raw_dir)
        pages = pdf_page_count(pdf_path)
        started = time.monotonic()

        credentials = ServicePrincipalCredentials(
            client_id=os.environ["PDF_SERVICES_CLIENT_ID"],
            client_secret=os.environ["PDF_SERVICES_CLIENT_SECRET"],
        )
        pdf_services = PDFServices(credentials=credentials)
        input_asset = pdf_services.upload(
            input_stream=pdf_path.read_bytes(),
            mime_type=PDFServicesMediaType.PDF,
        )
        params = ExtractPDFParams(
            elements_to_extract=[ExtractElementType.TEXT, ExtractElementType.TABLES],
            elements_to_extract_renditions=[
                ExtractRenditionsElementType.TABLES,
                ExtractRenditionsElementType.FIGURES,
            ],
            table_structure_type=TableStructureType.CSV,
        )
        job = ExtractPDFJob(input_asset=input_asset, extract_pdf_params=params)
        # submit() creates a transaction; do not replay it automatically after an
        # ambiguous timeout. The SDK itself has transport retries.
        location = pdf_services.submit(job)

        def fetch_result_bytes() -> bytes:
            response = pdf_services.get_job_result(location, ExtractPDFResult)
            result_asset: CloudAsset = response.get_result().get_resource()
            stream: StreamAsset = pdf_services.get_content(result_asset)
            return stream.get_input_stream()

        blob = self.retry_call(fetch_result_bytes, operation="adobe_get_job_result")
        latency = time.monotonic() - started
        zip_path = raw_dir / "extract.zip"
        zip_path.write_bytes(blob)
        extracted = ensure_dir(raw_dir / "extracted")
        with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
            zf.extractall(extracted)
        structured = extracted / "structuredData.json"
        if not structured.exists():
            raise ToolOutputParseError("Adobe result ZIP has no structuredData.json")

        tx = int(math.ceil(pages / 5.0))
        artifacts = [str(zip_path)] + [str(p) for p in extracted.rglob("*") if p.is_file()]
        return RawToolResult(
            primary_artifact=str(structured),
            artifacts=artifacts,
            metadata={
                "mock": False,
                "pages": pages,
                "latency_seconds": latency,
                "processing_seconds": latency,
                "document_transactions": tx,
                "estimated_cost_usd": 0.0 if bool(self.config.get("assume_free_tier", True)) else None,
                "actual_cost_usd": None,
                "pricing_basis": "Extract: 1 Document Transaction per up to 5 pages; free tier has 500 transactions/month",
            },
        )

    @staticmethod
    def _page_dims(payload: dict[str, Any]) -> dict[int, tuple[float, float]]:
        dims: dict[int, tuple[float, float]] = {}
        for i, p in enumerate(payload.get("pages") or []):
            num = int(p.get("pageNumber", p.get("Page", i)))
            width = float(p.get("width", p.get("Width", 1.0)) or 1.0)
            height = float(p.get("height", p.get("Height", 1.0)) or 1.0)
            dims[num] = (width, height)
        return dims

    @staticmethod
    def _find_csv(extracted_dir: Path, file_paths: list[str]) -> Path | None:
        for fp in file_paths:
            candidate = extracted_dir / fp
            if candidate.suffix.lower() == ".csv" and candidate.exists():
                return candidate
        return None

    @staticmethod
    def _csv_cells(path: Path) -> tuple[int, int, list[TableCell]]:
        rows = list(csv.reader(path.open("r", encoding="utf-8-sig", newline="")))
        ncols = max((len(r) for r in rows), default=0)
        cells = []
        for ri, row in enumerate(rows):
            for ci in range(ncols):
                cells.append(TableCell(row_index=ri, column_index=ci, text=row[ci] if ci < len(row) else ""))
        return len(rows), ncols, cells

    def standardize(self, raw_result, raw_dir, assets_dir, *, document_id, pdf_path):
        source = Path(raw_result.primary_artifact or "")
        extracted = raw_dir / "extracted"
        if raw_result.metadata.get("mock"):
            source = raw_dir / "mock_response.json"
            extracted = raw_dir
        payload = read_json(source)
        dims = self._page_dims(payload)
        # Adobe Page is zero-based in structuredData.json.
        pages_by_num: dict[int, Page] = {}

        def get_page(zero_page: int) -> Page:
            if zero_page not in pages_by_num:
                w, h = dims.get(zero_page, (1.0, 1.0))
                pages_by_num[zero_page] = Page(page_number=zero_page + 1, width=w, height=h)
            return pages_by_num[zero_page]

        table_seen: set[str] = set()
        for order, el in enumerate(payload.get("elements") or []):
            zero_page = int(el.get("Page", 0) or 0)
            page = get_page(zero_page)
            path = str(el.get("Path") or "")
            kind = adobe_path_kind(path)
            bounds = el.get("Bounds")
            bbox = normalize_bbox(bounds, page.width, page.height, origin="bottom-left") if bounds else None
            eid = f"adobe_p{page.page_number}_{order:05d}"
            provenance = {"path": path, "filePaths": el.get("filePaths", [])}

            # Only the terminal Table segment is a root; TR/TD descendants
            # remain separate native elements. Adobe numbers later tables.
            terminal = path.rstrip("/").rsplit("/", 1)[-1]
            if kind == "table" and re.fullmatch(r"Table(?:\[[0-9]+\])?", terminal, re.IGNORECASE):
                if path in table_seen:
                    continue
                table_seen.add(path)
                rows = cols = 0; cells: list[TableCell] = []
                csv_path = self._find_csv(extracted, list(el.get("filePaths") or []))
                if csv_path:
                    rows, cols, cells = self._csv_cells(csv_path)
                table = Table(
                    element_id=eid, page_number=page.page_number, bbox=bbox,
                    rows=rows, columns=cols, cells=cells, order_index=order,
                    provenance={**provenance, "csv": str(csv_path) if csv_path else None},
                )
                page.tables.append(table); page.reading_order.append(eid)
                continue

            if kind == "figure":
                asset_path = None
                for fp in el.get("filePaths") or []:
                    candidate = extracted / fp
                    if candidate.exists() and candidate.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                        assets_dir.mkdir(parents=True, exist_ok=True)
                        target = assets_dir / f"{eid}{candidate.suffix.lower()}"
                        shutil.copy2(candidate, target)
                        asset_path = str(target)
                        break
                image = ImageObject(
                    element_id=eid, page_number=page.page_number, bbox=bbox,
                    asset_path=asset_path, extraction_success=bool(asset_path), order_index=order,
                    provenance=provenance,
                )
                page.images.append(image); page.reading_order.append(eid)
                continue

            text = str(el.get("Text") or "")
            if not text:
                continue
            block_type = kind if kind in {"heading", "list_item", "caption", "paragraph"} else "paragraph"
            obj = TextBlock(
                element_id=eid, page_number=page.page_number, bbox=bbox,
                raw_text=text, block_type=block_type, order_index=order, provenance=provenance,
            )
            page.text_blocks.append(obj); page.reading_order.append(eid)

        if not pages_by_num:
            for i in range(int(raw_result.metadata.get("pages") or 1)):
                get_page(i)
        return StandardizedDocument(
            document_id=document_id, source_pdf=str(pdf_path), tool=self.tool_metadata(),
            pages=[pages_by_num[k] for k in sorted(pages_by_num)],
            metadata={"cloud_execution": raw_result.metadata},
        )
