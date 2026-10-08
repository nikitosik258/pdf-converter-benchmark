from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import BaseLocalAdapter, ToolExecutionError, ToolOutputParseError
from pdf_benchmark.models import (
    Caption,
    DiagramObject,
    Formula,
    ImageObject,
    Page,
    RawToolResult,
    StandardizedDocument,
    Table,
    TableCell,
    TextBlock,
)
from pdf_benchmark.utils.geometry import normalize_bbox
from pdf_benchmark.utils.io import read_json, relative_to_or_name, write_json
from pdf_benchmark.utils.versions import installed_versions
from pdf_benchmark.utils.text import collapse_ws, strip_math_wrappers


# Map the selected native class to the existing benchmark taxonomy. Unknown
# pictures remain images; this does not manufacture tables/chemical objects.
_PICTURE_DIAGRAM_TYPES = {
    "bar_chart": "chart",
    "box_plot": "chart",
    "line_chart": "chart",
    "pie_chart": "chart",
    "scatter_plot": "chart",
    "other_chart": "chart",
    "flow_chart": "flowchart",
    "engineering_drawing": "scientific_scheme",
}


class DoclingAdapter(BaseLocalAdapter):
    tool_name = "docling"
    distribution_name = "docling"
    pinned_version = "2.130.0"

    DEFAULT_CONFIG = {
        "do_ocr": True,
        "ocr_languages": ["iso:ru", "iso:en"],
        "do_table_structure": True,
        "table_mode": "accurate",
        "do_formula_enrichment": True,
        "do_picture_classification": True,
        "do_chart_extraction": True,
        "generate_page_images": True,
        "generate_picture_images": True,
        "images_scale": 1.5,
        "resource_sample_interval": 0.2,
    }

    def model_versions(self) -> dict[str, str]:
        return installed_versions(['docling', 'docling-core', 'docling-ibm-models', 'torch'])

    def __init__(self, config: dict[str, Any] | None = None, logger=None):
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(config or {})
        super().__init__(merged, logger=logger)

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                RapidOcrOptions,
                TableFormerMode,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling_core.types.doc import PictureItem, TableItem
        except Exception as exc:  # pragma: no cover
            raise ToolExecutionError("Docling is not installed") from exc

        native_assets = raw_dir / "native_assets"
        native_assets.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []

        try:
            opts = PdfPipelineOptions()
            opts.do_ocr = bool(self.config["do_ocr"])
            opts.do_table_structure = bool(self.config["do_table_structure"])
            opts.do_formula_enrichment = bool(self.config["do_formula_enrichment"])
            opts.generate_page_images = bool(self.config["generate_page_images"])
            opts.generate_picture_images = bool(self.config["generate_picture_images"])
            opts.images_scale = float(self.config["images_scale"])
            # The benchmark keeps Docling's automatic OCR selection.  The web
            # runner may explicitly request RapidOCR because the GPU image has
            # only its torch backend installed; automatic selection otherwise
            # rejects the mixed Russian/English language request.
            if self.config.get("ocr_engine") == "rapidocr":
                opts.ocr_options = RapidOcrOptions(
                    lang=list(self.config["ocr_languages"]),
                    backend=str(self.config.get("ocr_backend", "torch")),
                )
            else:
                opts.ocr_options.lang = list(self.config["ocr_languages"])
            if str(self.config["table_mode"]).lower() == "accurate":
                opts.table_structure_options.mode = TableFormerMode.ACCURATE
            else:
                opts.table_structure_options.mode = TableFormerMode.FAST
            if hasattr(opts, "do_picture_classification"):
                opts.do_picture_classification = bool(self.config["do_picture_classification"])
            if hasattr(opts, "do_chart_extraction"):
                opts.do_chart_extraction = bool(self.config["do_chart_extraction"])

            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
            result = converter.convert(pdf_path)
            doc = result.document
        except Exception as exc:
            raise ToolExecutionError(f"Docling conversion failed on {pdf_path.name}: {exc}") from exc

        doc_data = doc.export_to_dict() if hasattr(doc, "export_to_dict") else doc.model_dump(mode="json")
        doc_path = write_json(raw_dir / "document.json", doc_data)
        md_path = raw_dir / "document.md"
        md_path.write_text(doc.export_to_markdown(), encoding="utf-8")

        asset_map: dict[str, str] = {}
        picture_counter = 0
        table_counter = 0
        for element, _level in doc.iterate_items():
            try:
                if isinstance(element, PictureItem):
                    picture_counter += 1
                    out = native_assets / f"picture_{picture_counter:04d}.png"
                    image = element.get_image(doc)
                    if image is not None:
                        image.save(out, "PNG")
                        asset_map[str(element.self_ref)] = str(out)
                elif isinstance(element, TableItem):
                    table_counter += 1
                    out = native_assets / f"table_{table_counter:04d}.png"
                    image = element.get_image(doc)
                    if image is not None:
                        image.save(out, "PNG")
                        asset_map[str(element.self_ref)] = str(out)
            except Exception as exc:
                warnings.append(f"Could not export image for {getattr(element, 'self_ref', '?')}: {exc}")

        map_path = write_json(raw_dir / "asset_map.json", asset_map)
        artifacts = [doc_path, md_path, map_path, *native_assets.glob("*")]
        return RawToolResult(
            primary_artifact=str(doc_path),
            artifacts=[relative_to_or_name(p, raw_dir.parent) for p in artifacts],
            metadata={
                "conversion_status": str(getattr(result, "status", "unknown")),
                "picture_count": picture_counter,
                "table_count": table_counter,
            },
            warnings=warnings,
        )

    @staticmethod
    def _bbox_from_prov(doc: Any, prov: Any):
        if prov is None:
            return None
        page_item = doc.pages.get(prov.page_no)
        if page_item is None or page_item.size is None:
            return None
        width = float(page_item.size.width)
        height = float(page_item.size.height)
        b = prov.bbox
        origin = str(getattr(b, "coord_origin", "")).lower()
        if "bottom" in origin:
            raw = [b.l, b.b, b.r, b.t]
            return normalize_bbox(raw, width, height, origin="bottom-left")
        return normalize_bbox([b.l, b.t, b.r, b.b], width, height, origin="top-left")

    @classmethod
    def _first_bbox(cls, doc: Any, item: Any):
        prov = (getattr(item, "prov", None) or [None])[0]
        return cls._bbox_from_prov(doc, prov)

    @staticmethod
    def _page_no(item: Any) -> int:
        provs = getattr(item, "prov", None) or []
        return int(provs[0].page_no) if provs else 1

    @staticmethod
    def _caption(item: Any, doc: Any) -> Caption | None:
        try:
            text = collapse_ws(item.caption_text(doc))
        except Exception:
            text = ""
        return Caption(text=text) if text else None

    @staticmethod
    def _picture_text(item: Any, doc: Any) -> list[dict[str, str]]:
        """Retain native text ownership/order without inferring diagram structure."""
        from docling_core.types.doc import GroupItem, TextItem
        from docling_core.types.doc.labels import DocItemLabel

        excluded = {ref.cref for ref in [*item.captions, *item.footnotes]}
        visited = {item.self_ref}
        result: list[dict[str, str]] = []

        def visit(node: Any) -> None:
            for ref in node.children:
                if ref.cref in excluded or ref.cref in visited:
                    continue
                visited.add(ref.cref)
                child = ref.resolve(doc)
                if isinstance(child, TextItem):
                    if child.label in {DocItemLabel.CAPTION, DocItemLabel.FOOTNOTE}:
                        continue
                    text = collapse_ws(child.text)
                    if text:
                        result.append({"source_element_id": child.self_ref, "text": text})
                    visit(child)
                elif isinstance(child, GroupItem):
                    visit(child)
                # A nested picture/table owns its own content.

        visit(item)
        return result

    def standardize(
        self,
        raw_result: RawToolResult,
        raw_dir: Path,
        assets_dir: Path,
        *,
        document_id: str,
        pdf_path: Path,
    ) -> StandardizedDocument:
        try:
            from docling_core.types.doc import DoclingDocument, FormulaItem, PictureItem, TableItem, TextItem
        except Exception as exc:  # pragma: no cover
            raise ToolOutputParseError("docling-core is required to standardize Docling output") from exc

        if not raw_result.primary_artifact:
            raise ToolOutputParseError("Docling document.json is missing")
        data = read_json(raw_result.primary_artifact)
        doc = DoclingDocument.model_validate(data)
        asset_map = read_json(raw_dir / "asset_map.json") if (raw_dir / "asset_map.json").exists() else {}

        pages_by_no: dict[int, Page] = {}
        for key, p in doc.pages.items():
            pn = int(getattr(p, "page_no", key))
            size = getattr(p, "size", None)
            pages_by_no[pn] = Page(
                page_number=pn,
                width=float(getattr(size, "width", 1.0) or 1.0),
                height=float(getattr(size, "height", 1.0) or 1.0),
            )

        def get_page(pn: int) -> Page:
            if pn not in pages_by_no:
                pages_by_no[pn] = Page(page_number=pn, width=1.0, height=1.0)
            return pages_by_no[pn]

        order_per_page: dict[int, int] = {}
        for item, _level in doc.iterate_items():
            pn = self._page_no(item)
            page = get_page(pn)
            order = order_per_page.get(pn, 0)
            order_per_page[pn] = order + 1
            eid = str(getattr(item, "self_ref", f"p{pn}_{order}"))
            bbox = self._first_bbox(doc, item)

            if isinstance(item, FormulaItem):
                value = strip_math_wrappers(getattr(item, "text", ""))
                page.formulas.append(
                    Formula(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        raw_text=getattr(item, "orig", None) or getattr(item, "text", None),
                        latex=value or None,
                        plain_text=getattr(item, "orig", None),
                        formula_type="display",
                        order_index=order,
                        provenance={"label": str(getattr(item, "label", "formula"))},
                    )
                )
                page.reading_order.append(eid)
                continue

            if isinstance(item, TableItem):
                cells: list[TableCell] = []
                data_obj = item.data
                for cell in data_obj.table_cells:
                    cb = None
                    if getattr(cell, "bbox", None) is not None:
                        # Table-cell bboxes share the page coordinate system.
                        class _P: pass
                        fake = _P()
                        fake.page_no = pn
                        fake.bbox = cell.bbox
                        cb = self._bbox_from_prov(doc, fake)
                    cells.append(
                        TableCell(
                            row_index=int(cell.start_row_offset_idx),
                            column_index=int(cell.start_col_offset_idx),
                            row_span=max(1, int(getattr(cell, "row_span", cell.end_row_offset_idx - cell.start_row_offset_idx))),
                            column_span=max(1, int(getattr(cell, "col_span", cell.end_col_offset_idx - cell.start_col_offset_idx))),
                            text=str(getattr(cell, "text", "") or ""),
                            is_header=bool(getattr(cell, "column_header", False) or getattr(cell, "row_header", False)),
                            bbox=cb,
                        )
                    )
                page.tables.append(
                    Table(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        rows=int(getattr(data_obj, "num_rows", 0) or 0),
                        columns=int(getattr(data_obj, "num_cols", 0) or 0),
                        cells=cells,
                        caption=self._caption(item, doc),
                        order_index=order,
                        provenance={"self_ref": eid},
                    )
                )
                page.reading_order.append(eid)
                continue

            if isinstance(item, PictureItem):
                src = asset_map.get(eid)
                asset_path = None
                ok = False
                if src and Path(src).exists():
                    dest = assets_dir / Path(src).name
                    shutil.copy2(src, dest)
                    asset_path = relative_to_or_name(dest, assets_dir.parent)
                    ok = True
                meta = {}
                try:
                    if getattr(item, "meta", None) is not None:
                        meta = item.meta.model_dump(mode="json")
                except Exception:
                    pass
                classification = getattr(getattr(item, "meta", None), "classification", None)
                # Docling chooses maximum confidence, or the first prediction
                # when no confidence is available (also its tie convention).
                selected = (
                    classification.get_main_prediction()
                    if classification is not None and classification.predictions else None
                )
                dtype = _PICTURE_DIAGRAM_TYPES.get(selected.class_name if selected else None)
                picture_text = self._picture_text(item, doc)
                provenance = {
                    "docling_meta": meta,
                    "selected_classification": selected.model_dump(mode="json") if selected else None,
                    "classification_mapping": "native_top1_v1",
                    "picture_text_elements": picture_text,
                }
                caption = self._caption(item, doc)
                if dtype is not None:
                    page.diagrams.append(
                        DiagramObject(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            diagram_type=dtype,
                            text_elements=[entry["text"] for entry in picture_text],
                            asset_path=asset_path,
                            caption=caption,
                            order_index=order,
                            provenance={**provenance, "extraction_success": ok},
                        )
                    )
                else:
                    page.images.append(
                        ImageObject(
                            element_id=eid,
                            page_number=pn,
                            bbox=bbox,
                            asset_path=asset_path,
                            caption=caption,
                            extraction_success=ok,
                            order_index=order,
                            provenance=provenance,
                        )
                    )
                page.reading_order.append(eid)
                continue

            if isinstance(item, TextItem):
                text = collapse_ws(getattr(item, "text", ""))
                if not text:
                    continue
                label = str(getattr(item, "label", "")).lower()
                block_type = "unknown"
                if "title" in label or "section_header" in label:
                    block_type = "heading"
                elif "list" in label:
                    block_type = "list_item"
                elif "caption" in label:
                    block_type = "caption"
                elif "page_header" in label:
                    block_type = "header"
                elif "page_footer" in label:
                    block_type = "footer"
                else:
                    block_type = "paragraph"
                page.text_blocks.append(
                    TextBlock(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        raw_text=text,
                        block_type=block_type,
                        order_index=order,
                        provenance={"label": str(getattr(item, "label", ""))},
                    )
                )
                page.reading_order.append(eid)

        return StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path),
            tool=self.tool_metadata(),
            pages=[pages_by_no[k] for k in sorted(pages_by_no)],
            metadata={
                "capabilities": {
                    "text": True,
                    "tables": True,
                    "formulas": True,
                    "chemical_objects": False,
                    "images": True,
                    "diagrams": "picture classification/chart extraction when detected",
                    "reading_order": "Docling document tree / iterate_items",
                    "ocr": True,
                }
            },
        )
