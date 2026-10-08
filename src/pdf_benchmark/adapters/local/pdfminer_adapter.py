from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pdf_benchmark.adapters.base import (
    BaseLocalAdapter,
    ToolExecutionError,
    ToolOutputParseError,
)
from pdf_benchmark.models import (
    DiagramObject,
    ImageObject,
    Page,
    RawToolResult,
    StandardizedDocument,
    TextBlock,
)
from pdf_benchmark.utils.geometry import normalize_bbox
from pdf_benchmark.utils.io import (
    read_json,
    relative_to_or_name,
    write_json,
)
from pdf_benchmark.utils.text import collapse_ws
from pdf_benchmark.utils.versions import installed_versions


class PdfMinerAdapter(BaseLocalAdapter):
    """Lightweight pdfminer.six baseline.

    This adapter deliberately exposes only what pdfminer.six can infer directly
    and reproducibly without ML models:
      - positioned text blocks;
      - embedded raster images;
      - vector-heavy LTFigure regions as coarse diagram candidates.

    It does not claim semantic table, mathematical-formula, or chemical-object
    recognition. Those categories remain empty and are therefore scored as
    missing by the benchmark when no prediction exists.
    """

    tool_name = "pdfminer"
    distribution_name = "pdfminer.six"
    pinned_version = "20260107"

    DEFAULT_CONFIG = {
        "char_margin": 2.0,
        "line_margin": 0.5,
        "word_margin": 0.1,
        "boxes_flow": 0.5,
        "detect_vertical": True,
        "all_texts": False,
        "extract_images": True,
        "min_image_width": 8.0,
        "min_image_height": 8.0,
        "diagram_vector_min_objects": 4,
        "resource_sample_interval": 0.1,
    }

    def __init__(self, config: dict[str, Any] | None = None, logger=None):
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(config or {})
        super().__init__(merged, logger=logger)

    def model_versions(self) -> dict[str, str]:
        return installed_versions(["pdfminer.six"])

    @staticmethod
    def _bbox(obj: Any) -> list[float] | None:
        bbox = getattr(obj, "bbox", None)
        if bbox is None or len(bbox) != 4:
            return None
        return [float(x) for x in bbox]

    @staticmethod
    def _walk_with_parent(obj: Any, parent: Any = None):
        """Depth-first traversal retaining native parent identity."""
        yield obj, parent
        try:
            children = list(obj)
        except TypeError:
            return
        for child in children:
            yield from PdfMinerAdapter._walk_with_parent(child, obj)

    @staticmethod
    def _walk(obj: Any):
        for item, _parent in PdfMinerAdapter._walk_with_parent(obj):
            yield item

    @staticmethod
    def _text_hierarchy(blocks: list[dict[str, Any]]) -> tuple[set[int], dict[int, int]]:
        """Suppress only parents whose complete text survives in their children.

        New raw outputs carry parent indices. Older outputs were serialized in
        native preorder: infer Box -> Line links only from a contiguous prefix
        inside the box that reproduces its entire text. Text equality alone or
        the presence of other lines on the page never removes a container.
        """
        children: dict[int, list[int]] = {}
        if any("parent_text_index" in block for block in blocks):
            for idx, block in enumerate(blocks):
                parent = block.get("parent_text_index")
                if parent is None:
                    continue
                if type(parent) is not int or not 0 <= parent < idx:
                    raise ToolOutputParseError(f"Invalid pdfminer text parent {parent!r} at index {idx}")
                children.setdefault(parent, []).append(idx)
        else:
            for idx, block in enumerate(blocks):
                if not str(block.get("layout_type", "")).startswith("LTTextBox"):
                    continue
                parent_bbox = block.get("bbox")
                parent_text = collapse_ws(str(block.get("text", "")))
                if not parent_text or not parent_bbox:
                    continue
                parts, indices = [], []
                for child_idx in range(idx + 1, len(blocks)):
                    child = blocks[child_idx]
                    child_bbox = child.get("bbox")
                    if not str(child.get("layout_type", "")).startswith("LTTextLine") or not child_bbox:
                        break
                    # Numerical tolerance in native PDF points, not a relaxed
                    # matching threshold or a semantic geometry correction.
                    eps = 1e-8
                    if not (parent_bbox[0] <= child_bbox[0] + eps
                            and parent_bbox[1] <= child_bbox[1] + eps
                            and parent_bbox[2] >= child_bbox[2] - eps
                            and parent_bbox[3] >= child_bbox[3] - eps):
                        break
                    parts.append(str(child.get("text", "")))
                    indices.append(child_idx)
                    combined = collapse_ws(" ".join(parts))
                    if combined == parent_text:
                        children[idx] = indices
                        break
                    if not parent_text.startswith(combined):
                        break

        parents = {child: parent for parent, indices in children.items() for child in indices}
        represented = set()
        for parent, indices in children.items():
            original = collapse_ws(str(blocks[parent].get("text", "")))
            combined = collapse_ws(" ".join(str(blocks[i].get("text", "")) for i in indices))
            if original and original == combined:
                represented.add(parent)
        return represented, parents

    @classmethod
    def _figure_stats(cls, figure: Any) -> dict[str, int]:
        try:
            from pdfminer.layout import LTCurve, LTImage, LTLine, LTRect, LTTextContainer
        except Exception:
            return {"image_count": 0, "vector_count": 0, "text_count": 0}

        image_count = 0
        vector_count = 0
        text_count = 0
        for obj in cls._walk(figure):
            if obj is figure:
                continue
            if isinstance(obj, LTImage):
                image_count += 1
            elif isinstance(obj, (LTLine, LTRect, LTCurve)):
                vector_count += 1
            elif isinstance(obj, LTTextContainer):
                text_count += 1
        return {
            "image_count": image_count,
            "vector_count": vector_count,
            "text_count": text_count,
        }

    def run_raw(self, pdf_path: Path, raw_dir: Path) -> RawToolResult:
        try:
            from pdfminer.high_level import extract_pages
            from pdfminer.image import ImageWriter
            from pdfminer.layout import (
                LAParams,
                LTCurve,
                LTFigure,
                LTImage,
                LTLine,
                LTRect,
                LTTextContainer,
            )
        except Exception as exc:  # pragma: no cover
            raise ToolExecutionError("pdfminer.six is not installed") from exc

        native_assets = raw_dir / "native_assets"
        native_assets.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []

        boxes_flow = self.config.get("boxes_flow", 0.5)
        if boxes_flow is not None:
            boxes_flow = float(boxes_flow)

        laparams = LAParams(
            char_margin=float(self.config["char_margin"]),
            line_margin=float(self.config["line_margin"]),
            word_margin=float(self.config["word_margin"]),
            boxes_flow=boxes_flow,
            detect_vertical=bool(self.config["detect_vertical"]),
            all_texts=bool(self.config["all_texts"]),
        )

        image_writer = ImageWriter(str(native_assets))
        pages: list[dict[str, Any]] = []
        total_text = 0
        total_images = 0
        total_figures = 0

        try:
            for page_number, layout in enumerate(
                extract_pages(str(pdf_path), laparams=laparams),
                start=1,
            ):
                width = float(getattr(layout, "width", 1.0) or 1.0)
                height = float(getattr(layout, "height", 1.0) or 1.0)

                text_blocks: list[dict[str, Any]] = []
                images: list[dict[str, Any]] = []
                figures: list[dict[str, Any]] = []

                seen_text_ids: set[int] = set()
                text_indices: dict[int, int] = {}
                seen_image_ids: set[int] = set()
                seen_figure_ids: set[int] = set()

                text_order = 0
                image_order = 0
                figure_order = 0

                for obj, parent in self._walk_with_parent(layout):
                    if isinstance(obj, LTTextContainer) and id(obj) not in seen_text_ids:
                        seen_text_ids.add(id(obj))
                        text = collapse_ws(obj.get_text() or "")
                        if text:
                            text_indices[id(obj)] = text_order
                            text_blocks.append(
                                {
                                    "bbox": self._bbox(obj),
                                    "text": text,
                                    "layout_type": type(obj).__name__,
                                    "order_index": text_order,
                                    "parent_text_index": text_indices.get(id(parent)),
                                }
                            )
                            text_order += 1

                    elif isinstance(obj, LTImage) and id(obj) not in seen_image_ids:
                        seen_image_ids.add(id(obj))
                        x0, y0, x1, y1 = [float(x) for x in obj.bbox]
                        iw = abs(x1 - x0)
                        ih = abs(y1 - y0)
                        if (
                            iw < float(self.config["min_image_width"])
                            or ih < float(self.config["min_image_height"])
                        ):
                            continue

                        asset_rel = None
                        extraction_success = False
                        if bool(self.config["extract_images"]):
                            try:
                                filename = image_writer.export_image(obj)
                                exported = native_assets / filename
                                if exported.exists():
                                    asset_rel = str(Path("native_assets") / filename)
                                    extraction_success = True
                            except Exception as exc:
                                warnings.append(
                                    f"Could not export image {getattr(obj, 'name', '?')} "
                                    f"on page {page_number}: {exc}"
                                )

                        images.append(
                            {
                                "bbox": self._bbox(obj),
                                "name": str(getattr(obj, "name", "")),
                                "srcsize": list(getattr(obj, "srcsize", ()) or ()),
                                "bits": getattr(obj, "bits", None),
                                "asset_path": asset_rel,
                                "extraction_success": extraction_success,
                                "order_index": image_order,
                            }
                        )
                        image_order += 1

                    elif isinstance(obj, LTFigure) and id(obj) not in seen_figure_ids:
                        seen_figure_ids.add(id(obj))
                        stats = self._figure_stats(obj)
                        # Raster-only figures are represented through LTImage above.
                        # Here we retain only vector-heavy figures as coarse diagram
                        # candidates.
                        if (
                            stats["vector_count"]
                            >= int(self.config["diagram_vector_min_objects"])
                            and stats["image_count"] == 0
                        ):
                            figures.append(
                                {
                                    "bbox": self._bbox(obj),
                                    "name": str(getattr(obj, "name", "")),
                                    "order_index": figure_order,
                                    **stats,
                                }
                            )
                            figure_order += 1

                total_text += len(text_blocks)
                total_images += len(images)
                total_figures += len(figures)
                pages.append(
                    {
                        "page_number": page_number,
                        "width": width,
                        "height": height,
                        "text_blocks": text_blocks,
                        "images": images,
                        "figures": figures,
                    }
                )
        except Exception as exc:
            raise ToolExecutionError(
                f"pdfminer.six conversion failed on {pdf_path.name}: {exc}"
            ) from exc

        raw_path = write_json(
            raw_dir / "pdfminer_raw.json",
            {
                "schema_version": "1.0",
                "source_pdf": str(pdf_path),
                "pages": pages,
            },
        )
        artifacts = [raw_path, *native_assets.glob("*")]

        return RawToolResult(
            primary_artifact=str(raw_path),
            artifacts=[
                relative_to_or_name(p, raw_dir.parent)
                for p in artifacts
            ],
            metadata={
                "page_count": len(pages),
                "text_block_count": total_text,
                "image_count": total_images,
                "vector_figure_count": total_figures,
            },
            warnings=warnings,
        )

    def standardize(
        self,
        raw_result: RawToolResult,
        raw_dir: Path,
        assets_dir: Path,
        *,
        document_id: str,
        pdf_path: Path,
    ) -> StandardizedDocument:
        if not raw_result.primary_artifact:
            raise ToolOutputParseError("pdfminer_raw.json is missing")

        raw_path = Path(raw_result.primary_artifact)
        if not raw_path.exists():
            candidate = raw_dir / "pdfminer_raw.json"
            if candidate.exists():
                raw_path = candidate
            else:
                raise ToolOutputParseError(
                    f"pdfminer raw artifact does not exist: {raw_result.primary_artifact}"
                )

        payload = read_json(raw_path)
        page_models: list[Page] = []

        for page_data in payload.get("pages", []):
            pn = int(page_data["page_number"])
            width = float(page_data.get("width", 1.0) or 1.0)
            height = float(page_data.get("height", 1.0) or 1.0)
            page = Page(page_number=pn, width=width, height=height)

            order_entries: list[tuple[int, str]] = []

            native_blocks = page_data.get("text_blocks", [])
            represented_parents, parent_indices = self._text_hierarchy(native_blocks)
            for idx, block in enumerate(native_blocks):
                if idx in represented_parents:
                    continue
                raw_bbox = block.get("bbox")
                bbox = (
                    normalize_bbox(raw_bbox, width, height, origin="bottom-left")
                    if raw_bbox
                    else None
                )
                eid = f"p{pn}_txt_{idx:04d}"
                text = collapse_ws(str(block.get("text", "")))
                if not text:
                    continue
                order_index = int(block.get("order_index", idx))
                page.text_blocks.append(
                    TextBlock(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        raw_text=text,
                        block_type="paragraph",
                        order_index=order_index,
                        provenance={
                            "layout_type": block.get("layout_type"),
                            "parser": "pdfminer.six",
                            "source_block_index": idx,
                            "parent_text_index": parent_indices.get(idx),
                            "text_selection": "verified_text_hierarchy_v1",
                        },
                    )
                )
                order_entries.append((order_index, eid))

            for idx, image in enumerate(page_data.get("images", [])):
                raw_bbox = image.get("bbox")
                bbox = (
                    normalize_bbox(raw_bbox, width, height, origin="bottom-left")
                    if raw_bbox
                    else None
                )
                eid = f"p{pn}_img_{idx:04d}"
                asset_path = None
                extraction_success = bool(image.get("extraction_success"))

                rel = image.get("asset_path")
                if rel:
                    src = raw_dir / rel
                    if src.exists():
                        dest = assets_dir / src.name
                        shutil.copy2(src, dest)
                        asset_path = relative_to_or_name(dest, assets_dir.parent)
                        extraction_success = True

                order_index = 100000 + int(image.get("order_index", idx))
                page.images.append(
                    ImageObject(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        asset_path=asset_path,
                        caption=None,
                        extraction_success=extraction_success,
                        order_index=order_index,
                        provenance={
                            "name": image.get("name"),
                            "srcsize": image.get("srcsize"),
                            "bits": image.get("bits"),
                            "parser": "pdfminer.six",
                        },
                    )
                )
                order_entries.append((order_index, eid))

            for idx, figure in enumerate(page_data.get("figures", [])):
                raw_bbox = figure.get("bbox")
                bbox = (
                    normalize_bbox(raw_bbox, width, height, origin="bottom-left")
                    if raw_bbox
                    else None
                )
                eid = f"p{pn}_dia_{idx:04d}"
                order_index = 200000 + int(figure.get("order_index", idx))
                page.diagrams.append(
                    DiagramObject(
                        element_id=eid,
                        page_number=pn,
                        bbox=bbox,
                        diagram_type="vector_figure",
                        asset_path=None,
                        caption=None,
                        order_index=order_index,
                        provenance={
                            "name": figure.get("name"),
                            "vector_count": figure.get("vector_count", 0),
                            "image_count": figure.get("image_count", 0),
                            "text_count": figure.get("text_count", 0),
                            "extraction_success": False,
                            "parser": "pdfminer.six",
                        },
                    )
                )
                order_entries.append((order_index, eid))

            page.reading_order = [
                eid for _order, eid in sorted(order_entries, key=lambda x: x[0])
            ]
            page_models.append(page)

        return StandardizedDocument(
            document_id=document_id,
            source_pdf=str(pdf_path),
            tool=self.tool_metadata(),
            pages=page_models,
            metadata={
                "capabilities": {
                    "text": True,
                    "tables": False,
                    "formulas": False,
                    "chemical_objects": False,
                    "images": True,
                    "diagrams": "coarse vector-figure detection only",
                    "reading_order": "pdfminer.six LAParams layout order",
                    "ocr": False,
                },
                "baseline_role": (
                    "lightweight layout parser; unsupported semantic categories "
                    "are intentionally left empty rather than hallucinated"
                ),
            },
        )
