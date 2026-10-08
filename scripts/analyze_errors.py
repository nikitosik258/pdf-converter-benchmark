"""Prompt 14: reproducible object-level error analysis for the official benchmark.

The script is read-only with respect to PDFs, Ground Truth, benchmark caches and
experiment outputs.  It selects one lowest-scoring object in every category for
every active tool, renders the annotated PDF region, and combines the saved
match, prediction and metric components into Markdown/HTML/CSV/JSON artifacts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
TOOLS = (
    "pymupdf", "pdfplumber", "docling", "pdfminer", "mineru",
    "ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse",
)
NAMES = {
    "pymupdf": "PyMuPDF", "pdfplumber": "pdfplumber", "docling": "Docling",
    "pdfminer": "pdfminer.six", "mineru": "MinerU", "ocr_space": "OCR.Space",
    "nutrient": "Nutrient", "mindee": "Mindee", "adobe_extract": "Adobe Extract",
    "llamaparse": "LlamaParse",
}
CATEGORIES = ("text", "table", "math", "chemistry", "image", "diagram")
LABELS = {
    "text": "Text", "table": "Tables", "math": "Math",
    "chemistry": "Chemistry", "image": "Images", "diagram": "Diagrams",
}
PAGE_FIELDS = {
    "text_blocks": "TextBlock", "tables": "Table", "formulas": "Formula",
    "chemical_objects": "ChemicalObject", "images": "Image", "diagrams": "Diagram",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def bbox_dict(value: Any) -> dict[str, float] | None:
    if isinstance(value, list) and len(value) == 4:
        return dict(zip(("x_min", "y_min", "x_max", "y_max"), map(float, value)))
    if isinstance(value, dict) and all(key in value for key in ("x_min", "y_min", "x_max", "y_max")):
        return {key: float(value[key]) for key in ("x_min", "y_min", "x_max", "y_max")}
    return None


def overlap_fraction(reference: dict[str, float], candidate: dict[str, float]) -> float:
    width = max(0.0, min(reference["x_max"], candidate["x_max"]) - max(reference["x_min"], candidate["x_min"]))
    height = max(0.0, min(reference["y_max"], candidate["y_max"]) - max(reference["y_min"], candidate["y_min"]))
    area = max(1e-12, (reference["x_max"] - reference["x_min"]) * (reference["y_max"] - reference["y_min"]))
    return width * height / area


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text") or value.get("normalized_text") or "")
    return str(value)


def table_grid(value: dict[str, Any]) -> str:
    rows = int(value.get("rows") or 0)
    columns = int(value.get("columns") or 0)
    cells = value.get("cells") or []
    grid = [["" for _ in range(columns)] for _ in range(rows)]
    for cell in cells:
        row = int(cell.get("row_index", 0)); column = int(cell.get("column_index", 0))
        if 0 <= row < rows and 0 <= column < columns:
            prefix = "[H] " if cell.get("is_header") else ""
            grid[row][column] = prefix + text_value(cell.get("text"))
    lines = [f"Размер: {rows} × {columns}; ячеек: {len(cells)}"]
    lines.extend(" | ".join(item.replace("\n", " ") for item in row) for row in grid)
    return "\n".join(lines)


def describe_payload(category: str, value: dict[str, Any] | None) -> str:
    if not value:
        return "Типизированный объект отсутствует."
    if category == "text":
        return text_value(value.get("raw_text") or value.get("text"))
    if category == "table":
        return table_grid(value)
    if category == "math":
        payload = text_value(value.get("latex") or value.get("normalized_latex") or value.get("plain_text") or value.get("raw_text"))
        return payload or "Formula spatially matched, но математический payload пуст."
    if category == "chemistry":
        if value.get("raw_formula") or value.get("formula"):
            return "Формула: " + text_value(value.get("raw_formula") or value.get("formula"))
        labels = value.get("text_labels") or []
        name = value.get("name") or value.get("subtype") or "структурная формула"
        return f"{name}; текстовые метки: {', '.join(labels) if labels else 'нет'}; asset: {value.get('asset_path') or value.get('reference_image') or 'нет'}"
    if category == "image":
        return f"caption: {text_value(value.get('caption')) or 'нет'}; asset: {value.get('asset_path') or value.get('reference_image') or 'нет'}"
    if category == "diagram":
        return (
            f"тип: {value.get('diagram_type') or 'не указан'}\n"
            f"caption: {text_value(value.get('caption')) or 'нет'}\n"
            f"текст: {', '.join(value.get('text_elements') or []) or 'нет'}\n"
            f"ключевые элементы/связи: {', '.join(map(str, value.get('key_elements') or [])) or 'нет'}\n"
            f"asset: {value.get('asset_path') or value.get('reference_image') or 'нет'}"
        )
    return json.dumps(value, ensure_ascii=False)


def nearby_outputs(normalized: dict[str, Any], page_number: int, reference_bbox: dict[str, float]) -> list[dict[str, Any]]:
    page = next(page for page in normalized["pages"] if int(page["page_number"]) == page_number)
    found: list[dict[str, Any]] = []
    for field, type_name in PAGE_FIELDS.items():
        for item in page.get(field, []):
            candidate_bbox = bbox_dict(item.get("bbox"))
            if candidate_bbox is None:
                continue
            overlap = overlap_fraction(reference_bbox, candidate_bbox)
            center_x = (candidate_bbox["x_min"] + candidate_bbox["x_max"]) / 2
            center_y = (candidate_bbox["y_min"] + candidate_bbox["y_max"]) / 2
            center_inside = (
                reference_bbox["x_min"] <= center_x <= reference_bbox["x_max"]
                and reference_bbox["y_min"] <= center_y <= reference_bbox["y_max"]
            )
            if overlap < 0.002 and not center_inside:
                continue
            summary_category = {
                "TextBlock": "text", "Table": "table", "Formula": "math",
                "ChemicalObject": "chemistry", "Image": "image", "Diagram": "diagram",
            }[type_name]
            found.append({
                "type": type_name,
                "element_id": item.get("element_id"),
                "reference_overlap": overlap,
                "summary": describe_payload(summary_category, item)[:800],
                "asset_path": item.get("asset_path"),
            })
    return sorted(found, key=lambda item: (-item["reference_overlap"], item["type"], str(item["element_id"])))[:8]


def metric_map(evaluation: dict[str, Any]) -> dict[str, float]:
    return {item["metric_name"]: float(item["metric_value"]) for item in evaluation["metrics"]}


def classify_text(reference: str, prediction: str, metrics: dict[str, float], matched: bool,
                  nearby: list[dict[str, Any]]) -> tuple[list[str], str]:
    if not matched:
        text_count = sum(item["type"] == "TextBlock" for item in nearby)
        if text_count:
            return ["текст не собран в GT-регион", "потеря строк/reading order"], (
                f"Внутри области есть TextBlock: {text_count}, но matcher не смог собрать допустимое "
                "одно- или многоблочное prediction; все текстовые компоненты объекта равны нулю."
            )
        return ["пропуск текста"], "В размеченной области не найден TextBlock, поэтому все текстовые компоненты равны нулю."
    tags: list[str] = []
    ratio = len(prediction) / max(1, len(reference))
    if ratio < 0.72:
        tags.append("потеря символов/строк")
    if ratio > 1.35:
        tags.append("смешивание с соседним текстом")
    if metrics.get("wer", 100) + 20 < metrics.get("cer", 100):
        tags.append("reading order или разбиение слов")
    if ("-\n" in reference) != ("-\n" in prediction) or ("­" in reference) != ("­" in prediction):
        tags.append("сломанные переносы")
    if metrics.get("cer", 100) < 95:
        tags.append("ошибки OCR/символьные замены")
    if not tags:
        tags.append("незначительные переносы/пунктуация")
    return tags, (
        f"Длина output составляет {ratio:.2f} от GT; CER-quality={metrics.get('cer', 0):.2f}, "
        f"WER-quality={metrics.get('wer', 0):.2f}. Классификация причины основана на фактическом "
        "соотношении длин и разрыве между посимвольной и пословной метриками."
    )


def classify_table(reference: dict[str, Any], prediction: dict[str, Any] | None, metrics: dict[str, float], nearby: list[dict[str, Any]]) -> tuple[list[str], str]:
    if not prediction:
        text_count = sum(item["type"] == "TextBlock" for item in nearby)
        if text_count:
            return ["таблица превращена в обычный текст", "структура строк/колонок отсутствует"], (
                f"Typed Table отсутствует, но внутри GT-области найдено TextBlock: {text_count}. "
                "Текст извлечён без сетки ячеек, поэтому cell recall и структурные компоненты обнуляются."
            )
        image_count = sum(item["type"] in {"Image", "Diagram"} for item in nearby)
        if image_count:
            return ["таблица сохранена как изображение", "структура строк/колонок отсутствует"], (
                f"Typed Table отсутствует; в области найдено visual candidate: {image_count}. "
                "Растровое представление не содержит rows/columns/cells."
            )
        return ["пропуск таблицы"], "В области нет типизированной таблицы; строки, колонки и headers не восстановлены."
    tags: list[str] = []
    rr, rc = int(reference.get("rows", 0)), int(reference.get("columns", 0))
    pr, pc = int(prediction.get("rows", 0)), int(prediction.get("columns", 0))
    if pr < rr: tags.append("потеря строк")
    if pc < rc: tags.append("потеря колонок")
    ref_headers = sum(bool(cell.get("is_header")) for cell in reference.get("cells", []))
    pred_headers = sum(bool(cell.get("is_header")) for cell in prediction.get("cells", []))
    if pred_headers < ref_headers: tags.append("исчезновение headers")
    if metrics.get("merged_f1", 100) < 80: tags.append("ошибки объединения ячеек")
    if metrics.get("cell_content", 100) < 70: tags.append("искажение содержимого ячеек")
    if not tags: tags.append("структурное/текстовое расхождение")
    return tags, (
        f"GT={rr}×{rc}, output={pr}×{pc}; headers {ref_headers}→{pred_headers}. "
        f"Cell recall={metrics.get('cell_recall', 0):.2f}, TEDS-like={metrics.get('teds_like', 0):.2f}."
    )


def classify_math(reference: str, prediction: str, metrics: dict[str, float], matched: bool, nearby: list[dict[str, Any]]) -> tuple[list[str], str]:
    if not matched:
        nearby_types = Counter(item["type"] for item in nearby)
        if nearby_types.get("Image") or nearby_types.get("Diagram"):
            return ["формула возвращена как Figure/Image", "нет структурированного Math payload"], (
                "В области есть графический объект, но нет Formula с LaTeX/plain-math представлением; "
                "типобезопасный matcher не переименовывает generic Figure в формулу."
            )
        return ["формула не выделена"], "В области отсутствует Formula; token recall и edit similarity равны нулю."
    tags: list[str] = []
    checks = (
        ("\\frac", "потеря fraction structure"),
        ("_", "ошибки subscript"),
        ("^", "ошибки superscript"),
    )
    for token, label in checks:
        if token in reference and token not in prediction:
            tags.append(label)
    greek = ("alpha", "beta", "gamma", "delta", "lambda", "mu", "sigma", "omega", "Gamma", "Delta")
    if any(("\\" + token) in reference for token in greek) and not any(("\\" + token) in prediction for token in greek):
        tags.append("потеря Greek symbols")
    operators = ("\\sum", "\\int", "\\partial", "\\prod", "\\lim", "\\nabla", "=", "+", "-")
    if any(token in reference for token in operators) and sum(token in prediction for token in operators) < sum(token in reference for token in operators):
        tags.append("потеря operators")
    if reference.count("(") + reference.count("[") + reference.count("{") > prediction.count("(") + prediction.count("[") + prediction.count("{"):
        tags.append("потеря brackets")
    if not tags: tags.append("искажение токенов/номера формулы")
    return tags, (
        f"Exact match отсутствует; token precision={metrics.get('token_precision', 0):.2f}, "
        f"token recall={metrics.get('token_recall', 0):.2f}, edit similarity={metrics.get('edit_similarity', 0):.2f}."
    )


def classify_chemistry(reference: dict[str, Any], prediction: dict[str, Any] | None, metrics: dict[str, float], matched: bool) -> tuple[list[str], str]:
    if not matched or not prediction:
        return ["химический объект не обнаружен"], "Detection равен нулю; payload не участвует в структурированной оценке."
    if "text_labels" in reference:
        tags = []
        if metrics.get("label_recall", 100) < 100: tags.append("потеря химических символов/меток")
        if metrics.get("extraction", 100) == 0: tags.append("structural formula не извлечена")
        if metrics.get("iou", 100) < 60: tags.append("неточный crop")
        return tags or ["неполное структурное представление"], (
            f"Structure detection есть, но IoU={metrics.get('iou', 0):.2f}, "
            f"label recall={metrics.get('label_recall', 0):.2f}, extraction={metrics.get('extraction', 0):.2f}."
        )
    ref = text_value(reference.get("raw_formula") or reference.get("formula"))
    pred = text_value(prediction.get("raw_formula") or prediction.get("formula"))
    tags = []
    if any(ch.isdigit() for ch in ref) and sum(ch.isdigit() for ch in pred) < sum(ch.isdigit() for ch in ref): tags.append("ошибки индексов")
    if any(ch in ref for ch in "+−-") and not any(ch in pred for ch in "+−-"): tags.append("потеря charge")
    if metrics.get("composition_similarity", 100) < 100: tags.append("ошибки химических символов")
    return tags or ["искажение формулы"], (
        f"GT={ref!r}, output={pred!r}; token F1={metrics.get('token_f1', 0):.2f}, "
        f"composition similarity={metrics.get('composition_similarity', 0):.2f}."
    )


def classify_image(prediction: dict[str, Any] | None, metrics: dict[str, float], nearby: list[dict[str, Any]]) -> tuple[list[str], str]:
    if not prediction:
        visual = [item for item in nearby if item["type"] in {"Image", "Diagram"}]
        if visual:
            return ["visual candidate не сопоставлен с Image GT"], "В области есть visual candidate, но он не выбран при one-to-one сопоставлении по типу/геометрии либо не имеет подтверждённого extraction payload."
        text_count = sum(item["type"] == "TextBlock" for item in nearby)
        if text_count:
            return ["в области остался только текст", "потеря visual payload"], (
                f"В области найдено TextBlock: {text_count}, но отсутствует Image asset; OCR-текст, caption или alt-text не заменяют извлечение изображения."
            )
        return ["пропуск изображения"], "Image-кандидат в размеченной области отсутствует; detection, extraction и caption равны нулю."
    tags = []
    if metrics.get("iou", 100) < 60: tags.append("неправильный crop")
    if metrics.get("extraction", 100) == 0: tags.append("изображение не сохранено")
    if metrics.get("caption", 100) < 70: tags.append("потеря caption")
    return tags or ["частичное расхождение изображения"], (
        f"Detection={metrics.get('detection_recall', 0):.2f}, IoU={metrics.get('iou', 0):.2f}, "
        f"extraction={metrics.get('extraction', 0):.2f}, caption={metrics.get('caption', 0):.2f}."
    )


def classify_diagram(prediction: dict[str, Any] | None, metrics: dict[str, float], nearby: list[dict[str, Any]]) -> tuple[list[str], str]:
    if not prediction:
        image_count = sum(item["type"] == "Image" for item in nearby)
        if image_count:
            return ["диаграмма сохранена как обычное изображение", "потеря текста и связей"], (
                f"В GT-области найдено Image: {image_count}, но Diagram отсутствует. "
                "Generic image не содержит типизированных text_elements/key_elements."
            )
        text_count = sum(item["type"] == "TextBlock" for item in nearby)
        if text_count:
            return ["диаграмма представлена только текстом/Markdown", "связи не типизированы"], (
                f"В GT-области найдено TextBlock: {text_count}, но Diagram отсутствует. "
                "OCR-текст или Markdown/Mermaid не дают benchmark typed key_elements без отдельного преобразования."
            )
        return ["пропуск диаграммы", "потеря текста и связей"], "Diagram-кандидат отсутствует, поэтому detection и все семантические компоненты равны нулю."
    tags = []
    if metrics.get("diagram_text_recall", 100) < 80: tags.append("потеря текста")
    if metrics.get("key_elements", 100) < 80: tags.append("потеря связей/ключевых элементов")
    if metrics.get("caption", 100) < 70: tags.append("потеря caption")
    if metrics.get("iou", 100) < 60: tags.append("неправильный crop")
    return tags or ["неполная диаграмма"], (
        f"Detection={metrics.get('detection_recall', 0):.2f}, IoU={metrics.get('iou', 0):.2f}, "
        f"diagram text recall={metrics.get('diagram_text_recall', 0):.2f}, "
        f"key elements={metrics.get('key_elements', 0):.2f}."
    )


def classify(category: str, reference: dict[str, Any], prediction: dict[str, Any] | None,
             evaluation: dict[str, Any], match: dict[str, Any], nearby: list[dict[str, Any]]) -> tuple[list[str], str]:
    metrics = metric_map(evaluation)
    if category == "text":
        return classify_text(describe_payload(category, reference), describe_payload(category, prediction), metrics, bool(match["matched"]), nearby)
    if category == "table":
        return classify_table(reference, prediction, metrics, nearby)
    if category == "math":
        return classify_math(describe_payload(category, reference), describe_payload(category, prediction), metrics, bool(match["matched"]), nearby)
    if category == "chemistry":
        return classify_chemistry(reference, prediction, metrics, bool(match["matched"]))
    if category == "image":
        return classify_image(prediction, metrics, nearby)
    return classify_diagram(prediction, metrics, nearby)


def metric_impact(category: str, evaluation: dict[str, Any]) -> str:
    metrics = metric_map(evaluation)
    names = {
        "text": ("cer", "wer", "edit_similarity"),
        "table": ("cell_recall", "cell_content", "row_structure", "column_structure", "merged_f1", "teds_like"),
        "math": ("raw_exact_match", "normalized_exact_match", "token_precision", "token_recall", "token_f1", "edit_similarity"),
        "chemistry": ("detection", "token_f1", "composition_similarity", "iou", "label_recall", "extraction", "structured_extraction"),
        "image": ("detection_recall", "iou", "extraction", "caption"),
        "diagram": ("detection_recall", "iou", "diagram_text_recall", "caption", "element_recall", "key_elements"),
    }[category]
    parts = [f"{name}={metrics[name]:.2f}" for name in names if name in metrics]
    score = float(evaluation["object_score"])
    return f"Object Score={score:.2f}/100; потеря относительно идеального объекта={100-score:.2f} п.п.; " + ", ".join(parts)


def render_crop(pdf_path: Path, page_number: int, bbox: dict[str, float], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as document:
        page = document[page_number - 1]
        rect = page.rect
        x0, y0 = bbox["x_min"] * rect.width, bbox["y_min"] * rect.height
        x1, y1 = bbox["x_max"] * rect.width, bbox["y_max"] * rect.height
        pad_x = max(6.0, (x1 - x0) * 0.04); pad_y = max(6.0, (y1 - y0) * 0.08)
        clip = pymupdf.Rect(x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y) & rect
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        pixmap.save(target)


def copy_prediction_asset(pair: dict[str, Any], tool: str, object_id: str,
                          prediction: dict[str, Any] | None, output: Path) -> str | None:
    if not prediction or not prediction.get("asset_path"):
        return None
    value = Path(str(prediction["asset_path"]).replace("\\", "/"))
    source = value if value.is_absolute() else Path(pair["cache_dir"]) / value
    if not source.is_file():
        return None
    suffix = source.suffix.lower() or ".bin"
    target = output / "prediction_assets" / f"{tool}_{object_id}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target.relative_to(output).as_posix()


def excerpt(value: str, limit: int = 2400) -> str:
    clean = value.replace("```", "''' ")
    return clean if len(clean) <= limit else clean[:limit] + "\n… [полное значение в examples.json]"


def render_reports(output: Path, experiment_id: str, rows: list[dict[str, Any]], summary: list[dict[str, Any]]) -> None:
    md = [
        "# Промт 14 — подробный error analysis",
        "",
        f"Эксперимент: `{experiment_id}`. Выбрано {len(rows)} примеров: по шесть категорий для каждого из десяти инструментов.",
        "",
        "Выбор детерминирован: в каждой паре tool × category взят объект с минимальным Object Score; при равенстве — первый по ID. Это стратифицированная диагностическая выборка, а не новая метрика и не изменение benchmark.",
        "",
        "## Покрытие",
        "",
        "| Инструмент | Примеров | Категории | Пропущено |",
        "|---|---:|---|---:|",
    ]
    for item in summary:
        md.append(f"| {item['tool_name']} | {item['examples']} | {item['categories']} | {item['unmatched']} |")
    md.extend([
        "",
        "Полные машиночитаемые значения: [examples.csv](examples.csv), [examples.json](examples.json). Изображение «Исходный объект» — рендер точного GT bbox с небольшим контекстом.",
        "",
    ])

    html_parts = ["""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Промт 14 — Error analysis</title><style>
body{font:15px/1.55 system-ui,Segoe UI,sans-serif;color:#183045;background:#f3f5f7;margin:0}main{max-width:1250px;margin:auto;background:white;padding:32px}h1{margin-top:0}h2{margin-top:48px;border-bottom:1px solid #ccd8de;padding-bottom:8px}.case{border:1px solid #d9e2e7;border-radius:10px;padding:18px;margin:22px 0;background:#fbfcfd}.meta{color:#536779}.source{max-width:100%;max-height:560px;border:1px solid #d9e2e7}.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}.payload{white-space:pre-wrap;overflow:auto;max-height:420px;background:#eef3f5;padding:12px;border-radius:6px;font:13px/1.45 Consolas,monospace}.tags{font-weight:650;color:#8c3d19}.impact{font-family:Consolas,monospace;font-size:13px}.asset{max-width:100%;max-height:420px}@media(max-width:800px){.cols{grid-template-columns:1fr}}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #dde5e9;padding:8px;text-align:left}</style></head><body><main>""",
        f"<h1>Промт 14 — подробный error analysis</h1><p>Эксперимент <code>{html.escape(experiment_id)}</code>. {len(rows)} примеров: шесть категорий для каждого инструмента.</p>",
        "<p>В каждой паре tool × category выбран объект с минимальным Object Score. Исходный объект — рендер GT bbox; полные данные доступны в <a href='examples.json'>examples.json</a>.</p>",
        "<table><thead><tr><th>Инструмент</th><th>Примеров</th><th>Категории</th><th>Пропущено</th></tr></thead><tbody>",
    ]
    for item in summary:
        html_parts.append(f"<tr><td>{html.escape(item['tool_name'])}</td><td>{item['examples']}</td><td>{html.escape(item['categories'])}</td><td>{item['unmatched']}</td></tr>")
    html_parts.append("</tbody></table>")

    for tool in TOOLS:
        tool_rows = [row for row in rows if row["tool"] == tool]
        md.append(f"## {NAMES[tool]}")
        html_parts.append(f"<h2>{html.escape(NAMES[tool])}</h2>")
        for index, row in enumerate(tool_rows, start=1):
            title = f"{index}. {row['category_label']} — {row['object_id']} ({row['document_id']}, стр. {row['page']})"
            md.extend([
                "", f"### {title}", "",
                f"![Исходный объект]({row['source_crop']})", "",
                f"**Ошибка:** {row['error_types']}", "",
                f"**Причина:** {row['cause']}", "",
                f"**Влияние на метрику:** {row['metric_impact']}", "",
                "**Ground Truth**", "", "```text", excerpt(row["ground_truth"]), "```", "",
                "**Output инструмента**", "", "```text", excerpt(row["tool_output"]), "```", "",
            ])
            if row.get("prediction_asset"):
                md.extend([f"![Извлечённый visual asset]({row['prediction_asset']})", ""])
            html_parts.extend([
                "<section class='case'>",
                f"<h3>{html.escape(title)}</h3>",
                f"<p class='meta'>matched={row['matched']} · method={html.escape(row['match_method'])} · Object Score={row['object_score']:.2f}</p>",
                f"<img class='source' src='{html.escape(row['source_crop'])}' alt='Исходный объект'>",
                f"<p class='tags'>Ошибка: {html.escape(row['error_types'])}</p>",
                f"<p><strong>Причина:</strong> {html.escape(row['cause'])}</p>",
                f"<p class='impact'><strong>Метрика:</strong> {html.escape(row['metric_impact'])}</p>",
                "<div class='cols'>",
                f"<div><h4>Ground Truth</h4><div class='payload'>{html.escape(excerpt(row['ground_truth']))}</div></div>",
                f"<div><h4>Output инструмента</h4><div class='payload'>{html.escape(excerpt(row['tool_output']))}</div></div>",
                "</div>",
            ])
            if row.get("prediction_asset"):
                html_parts.append(f"<h4>Извлечённый visual asset</h4><img class='asset' src='{html.escape(row['prediction_asset'])}' alt='Prediction asset'>")
            html_parts.append("</section>")
    md.extend([
        "## Методические ограничения", "",
        "Error labels являются воспроизводимой диагностической интерпретацией сохранённых prediction и metric components. Они не заменяют Object Score. Для Text отдельной GT reading-order разметки нет, поэтому формулировка «reading order или разбиение слов» используется только при большом разрыве CER/WER и не объявляет точную первопричину без визуальной проверки.",
        "",
        "Пропуск typed объекта отличён от наличия содержимого другим типом: nearby output перечисляется в output. Generic Image/TextBlock не переименовывается в Table, Formula или Diagram ради повышения score.",
    ])
    html_parts.append("<h2>Методические ограничения</h2><p>Error labels — диагностическая интерпретация сохранённых prediction и metric components. Для Text нет отдельной GT reading-order разметки; generic объекты не переименовываются ради score.</p></main></body></html>")
    (output / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (output / "report.html").write_text("\n".join(html_parts), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id")
    args = parser.parse_args()
    experiment_id = args.experiment_id or (ROOT / "outputs/benchmark/latest_experiment.txt").read_text(encoding="utf-8").strip()
    if not experiment_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in experiment_id):
        raise ValueError("Invalid experiment ID")

    experiment = ROOT / "outputs/benchmark/experiments" / experiment_id
    prompt14_root = (ROOT / "reports/error_analysis").resolve()
    output = (prompt14_root / experiment_id).resolve()
    if prompt14_root not in output.parents:
        raise ValueError("Prompt 14 output escaped the intended report root")
    if output.is_dir():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    tracked: dict[Path, str] = {}

    def track(path: Path) -> Path:
        tracked[path.resolve()] = sha256(path)
        return path

    manifest = read_json(track(experiment / "experiment_manifest.json"))
    quality = read_json(track(experiment / "results/data_quality_summary.json"))
    preflight = read_json(track(experiment / "preflight_report.json"))
    official_objects = {
        (row["tool"], row["document_id"], row["object_id"]): row
        for row in read_csv(track(experiment / "results/object_results_dataset.csv"))
    }
    gt_payload = read_json(track(ROOT / "ground_truth/objects.json"))
    gt = {item["object_id"]: item for item in gt_payload["objects"]}
    pdfs = {item["document_id"]: Path(item["path"]) for item in preflight["pdfs"]}
    assert manifest["status"] == quality["status"] == "clean"
    assert (quality["clean_tool_rows"], quality["pair_rows"], quality["object_rows"]) == (10, 50, 400)

    all_cases: list[dict[str, Any]] = []
    pair_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    normalized_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for tool in TOOLS:
        run_id = manifest["tool_runs"][tool]
        run = ROOT / "outputs/benchmark/runs" / run_id
        pairs = read_jsonl(track(run / "pair_results.jsonl"))
        assert len(pairs) == 5
        for pair in pairs:
            document_id = pair["document_id"]
            pair_by_key[tool, document_id] = pair
            pair_dir = run / "pairs" / tool / document_id
            matches = read_json(track(pair_dir / "matches.json"))
            evaluations = read_json(track(pair_dir / "object_evaluation.json"))
            normalized_path = Path(pair["paths"]["normalized"])
            normalized_by_key[tool, document_id] = read_json(track(normalized_path))
            match_map = {item["object_id"]: item for item in matches}
            for evaluation in evaluations:
                match = match_map[evaluation["object_id"]]
                official = official_objects[tool, document_id, evaluation["object_id"]]
                assert math.isclose(float(official["object_score"]), float(evaluation["object_score"]), abs_tol=1e-8)
                assert (official["matched"] == "True") == bool(match["matched"])
                all_cases.append({"tool": tool, "document_id": document_id, "match": match, "evaluation": evaluation})
    assert len(all_cases) == 400

    selected: list[dict[str, Any]] = []
    for tool in TOOLS:
        for category in CATEGORIES:
            candidates = [case for case in all_cases if case["tool"] == tool and case["evaluation"]["category"] == category]
            chosen = min(candidates, key=lambda case: (float(case["evaluation"]["object_score"]), case["evaluation"]["object_id"]))
            selected.append(chosen)
    assert len(selected) == 60

    unique_objects = {case["evaluation"]["object_id"] for case in selected}
    for object_id in unique_objects:
        item = gt[object_id]
        bbox = bbox_dict(item["bbox"])
        assert bbox is not None
        crop = output / "source_crops" / f"{object_id}.png"
        render_crop(track(pdfs[item["document_id"]]), int(item["page"]), bbox, crop)

    rows: list[dict[str, Any]] = []
    full_rows: list[dict[str, Any]] = []
    for case in selected:
        tool = case["tool"]; evaluation = case["evaluation"]; match = case["match"]
        object_id = evaluation["object_id"]; document_id = evaluation["document_id"]
        category = evaluation["category"]
        reference = match["reference"]
        prediction = match.get("prediction")
        reference_bbox = bbox_dict(reference.get("bbox") or gt[object_id]["bbox"])
        assert reference_bbox is not None
        nearby = nearby_outputs(normalized_by_key[tool, document_id], int(evaluation["page"]), reference_bbox)
        tags, cause = classify(category, reference, prediction, evaluation, match, nearby)
        gt_text = describe_payload(category, reference)
        prediction_text = describe_payload(category, prediction)
        if not prediction and nearby:
            prediction_text += "\n\nСодержимое в той же области, но другого/несопоставленного типа:\n" + "\n".join(
                f"- {item['type']} overlap={item['reference_overlap']:.2f}: {item['summary']}" for item in nearby
            )
        asset_payload = prediction or next(
            ({"asset_path": item["asset_path"]} for item in nearby if item.get("asset_path")),
            None,
        )
        prediction_asset = copy_prediction_asset(pair_by_key[tool, document_id], tool, object_id, asset_payload, output)
        record = {
            "tool": tool,
            "tool_name": NAMES[tool],
            "category": category,
            "category_label": LABELS[category],
            "document_id": document_id,
            "page": int(evaluation["page"]),
            "object_id": object_id,
            "object_type": evaluation["object_type"],
            "matched": bool(match["matched"]),
            "match_method": match["match_method"],
            "object_score": float(evaluation["object_score"]),
            "error_types": "; ".join(tags),
            "cause": cause,
            "metric_impact": metric_impact(category, evaluation),
            "ground_truth": gt_text,
            "tool_output": prediction_text,
            "source_crop": f"source_crops/{object_id}.png",
            "prediction_asset": prediction_asset,
        }
        rows.append(record)
        full_rows.append({**record, "reference_payload": reference, "prediction_payload": prediction, "nearby_output": nearby, "metrics": evaluation["metrics"]})

    summary = []
    for tool in TOOLS:
        subset = [row for row in rows if row["tool"] == tool]
        summary.append({
            "tool": tool, "tool_name": NAMES[tool], "examples": len(subset),
            "categories": ", ".join(row["category_label"] for row in subset),
            "unmatched": sum(not row["matched"] for row in subset),
        })
    assert all(item["examples"] == 6 for item in summary)
    write_csv(output / "examples.csv", rows)
    write_json(output / "examples.json", full_rows)
    write_csv(output / "tool_summary.csv", summary)
    render_reports(output, experiment_id, rows, summary)

    track(Path(__file__))
    changed = [str(path.relative_to(ROOT)) for path, digest in tracked.items() if sha256(path) != digest]
    manifest_out = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": experiment_id,
        "status": "complete" if not changed else "input_changed",
        "selection_policy": "lowest_object_score_per_tool_category_then_object_id_v1",
        "tools": len(TOOLS), "categories": len(CATEGORIES), "examples": len(rows),
        "examples_per_tool": {tool: sum(row["tool"] == tool for row in rows) for tool in TOOLS},
        "category_counts": dict(Counter(row["category"] for row in rows)),
        "matched_examples": sum(row["matched"] for row in rows),
        "unmatched_examples": sum(not row["matched"] for row in rows),
        "source_crops": len(unique_objects),
        "prediction_assets": sum(bool(row["prediction_asset"]) for row in rows),
        "adapter_executions": 0, "network_calls": 0,
        "inputs_changed": changed,
        "input_sha256": {str(path.relative_to(ROOT)): digest for path, digest in sorted(tracked.items(), key=lambda item: str(item[0]))},
    }
    write_json(output / "analysis_manifest.json", manifest_out)
    print(json.dumps({key: manifest_out[key] for key in ("status", "experiment_id", "tools", "categories", "examples", "matched_examples", "unmatched_examples", "source_crops", "prediction_assets", "inputs_changed")}, ensure_ascii=False, indent=2))
    return 0 if not changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
