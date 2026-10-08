"""Build Prompt 15 full technical report from the fixed benchmark artifacts.

This is a read-only aggregation step with respect to PDFs, Ground Truth,
benchmark caches, and experiment results.  It copies already generated figures
and diagnostic crops into a self-contained report directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = (
    "pymupdf", "pdfplumber", "docling", "pdfminer", "mineru",
    "ocr_space", "nutrient", "mindee", "adobe_extract", "llamaparse",
)
CATEGORIES = ("text", "table", "math", "chemistry", "image", "diagram")
CATEGORY_LABELS = {
    "text": "Текст", "table": "Таблицы", "math": "Математика",
    "chemistry": "Химия", "image": "Изображения", "diagram": "Диаграммы",
}

DOCS = {
    "pymupdf": "https://pymupdf.readthedocs.io/en/latest/",
    "pdfplumber": "https://github.com/jsvine/pdfplumber",
    "docling": "https://docling-project.github.io/docling/",
    "pdfminer": "https://pdfminersix.readthedocs.io/en/latest/",
    "mineru": "https://github.com/opendatalab/MinerU",
    "ocr_space": "https://ocr.space/ocrapi",
    "nutrient": "https://www.nutrient.io/api/data-extraction-api/",
    "mindee": "https://docs.mindee.com/",
    "adobe_extract": "https://developer.adobe.com/document-services/docs/overview/pdf-extract-api/",
    "llamaparse": "https://developers.llamaindex.ai/llamaparse/parse/getting_started/",
}

TOOL_NOTES = {
    "pymupdf": "Классический локальный PDF-парсер; извлечение текста, координат и встроенных изображений.",
    "pdfplumber": "Классическая локальная библиотека поверх pdfminer.six с доступом к словам, геометрии и таблицам.",
    "docling": "Локальная ML-система анализа layout, таблиц, формул и иллюстраций.",
    "pdfminer": "Классический локальный анализатор текстового слоя и геометрии PDF.",
    "mineru": "Локальная ML-система структурного разбора технических документов.",
    "ocr_space": "Облачный OCR; в эксперименте использован Engine 3 с TextOverlay.",
    "nutrient": "Облачный Data Extraction API; режим understand, spatial output.",
    "mindee": "Облачный V2 OCR / Raw Text OCR с текстом и координатами слов.",
    "adobe_extract": "Облачный PDF Extract API со структурированным JSON и renditions.",
    "llamaparse": "Облачный LlamaParse v2; tier agentic со структурным page/item output.",
}

ERROR_SUMMARY = {
    "pymupdf": "Текст почти точный; сложные таблицы остаются набором текстовых блоков; формулы теряют дроби и индексы; схемы типизируются как обычные изображения.",
    "pdfplumber": "В сложной вёрстке нарушаются порядок и разбиение слов; таблицы не всегда собираются из слов; математика становится плоским текстом; диаграммы не типизируются.",
    "docling": "На D05 заметны ошибки порядка/OCR; сложные таблицы теряют строки и объединения; MATH_006 повреждает дроби, индексы, греческие символы и операторы; у схем теряются подписи и связи.",
    "pdfminer": "D05 вызывает сильную потерю текста и порядка; таблицы остаются словами; формулы плоские и неполные; изображения извлекаются непоследовательно, диаграммы остаются generic image.",
    "mineru": "Ошибки текста связаны с порядком и OCR; таблицы теряют строки/merges; в формулах встречаются токенные ошибки; страдают индексы химии, crop и caption изображений.",
    "ocr_space": "Часть текстовых регионов не сопоставляется; таблицы не структурируются; OCR формул теряет дроби, индексы, греческие символы и операторы; визуальный payload отсутствует.",
    "nutrient": "На D05 нарушаются порядок и переносы; таблицы теряют строки/объединения; отдельный математический объект сопоставлен пространственно, но payload пуст; схемы часто остаются изображениями.",
    "mindee": "Слова D05 дробятся; таблицы представлены словами; математика плоская; химические индексы повреждаются; изображения и диаграммы представлены преимущественно текстом OCR.",
    "adobe_extract": "Сегментация слов ухудшается на D05; отдельные таблицы теряют структуру; математические объекты возвращаются как Figure без LaTeX; диаграммы остаются figures.",
    "llamaparse": "На D05 встречаются ошибки порядка и переносов; таблицы теряют строки и содержимое; математический payload не всегда точен; Mermaid-граф может остаться TextBlock, а изображения — описанием без visual asset.",
}

DOCUMENTS = {
    "D01": ("3619.pdf", 43, "Лабораторный практикум по измерительной технике и метрологии; таблицы, формулы, приборные изображения и схемы."),
    "D02": ("369.pdf", 7, "Химическая статья о магнитных сорбентах; линейные формулы и структурные формулы."),
    "D03": ("mais702.pdf", 14, "Статья о классификации токсичности комментариев нейронными сетями; многоколоночный текст, таблицы и графики."),
    "D04": ("sjim924.pdf", 12, "Статья о нейросетевом решении обратных задач аномальной диффузии; плотная математика, таблица и диаграмма."),
    "D05": ("ufn289.pdf", 22, "Обзор приэлектродных процессов в жидких диэлектриках; сканированный текст, формулы, таблица, фотографии и схема."),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fnum(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "—"
    return f"{float(value):.{digits}f}".replace(".", ",")


def esc_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


class Report:
    def __init__(self, output: Path, experiment_id: str) -> None:
        self.output = output
        self.experiment_id = experiment_id
        self.md: list[str] = []
        self.body: list[str] = []
        self.toc: list[tuple[str, str]] = []

    def heading(self, number: int, anchor: str, title: str) -> None:
        label = f"{number}. {title}"
        self.toc.append((anchor, label))
        self.md.append(f"\n## {label}\n")
        self.body.append(f'<h2 id="{anchor}">{html.escape(label)}</h2>')

    def subheading(self, title: str) -> None:
        self.md.append(f"\n### {title}\n")
        self.body.append(f"<h3>{html.escape(title)}</h3>")

    def paragraph(self, text: str) -> None:
        self.md.append(text + "\n")
        self.body.append(f"<p>{html.escape(text)}</p>")

    def bullets(self, items: list[str]) -> None:
        self.md.append("\n" + "\n".join(f"- {item}" for item in items) + "\n")
        self.body.append("<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>")

    def formula(self, latex: str, explanation: str = "") -> None:
        self.md.append(f"\n$$\n{latex}\n$$\n")
        if explanation:
            self.md.append(explanation + "\n")
        self.body.append(f'<div class="formula"><code>{html.escape(latex)}</code>' + (f"<p>{html.escape(explanation)}</p>" if explanation else "") + "</div>")

    def table(self, rows: list[dict[str, Any]], columns: list[tuple[str, str]], caption: str) -> None:
        self.md.extend([caption + "\n", "| " + " | ".join(label for _, label in columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"])
        for row in rows:
            self.md.append("| " + " | ".join(esc_md(row.get(key, "—")) for key, _ in columns) + " |")
        self.md.append("")
        self.body.append('<div class="table-wrap"><table><caption>' + html.escape(caption) + "</caption><thead><tr>" + "".join(f"<th>{html.escape(label)}</th>" for _, label in columns) + "</tr></thead><tbody>")
        for row in rows:
            self.body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(key, '—')))}</td>" for key, _ in columns) + "</tr>")
        self.body.append("</tbody></table></div>")

    def figure(self, filename: str, title: str, note: str) -> None:
        rel = f"figures/{filename}.png"
        self.md.append(f"![{title}]({rel})\n\n*{note}*\n")
        self.body.append(f'<figure><img src="{rel}" alt="{html.escape(title)}"><figcaption>{html.escape(note)} <a href="figures/{filename}.svg">SVG</a> · <a href="{rel}">PNG</a></figcaption></figure>')

    def link_paragraph(self, parts: list[tuple[str, str]]) -> None:
        self.md.append(" · ".join(f"[{label}]({url})" for label, url in parts) + "\n")
        self.body.append("<p class=\"links\">" + " · ".join(f'<a href="{html.escape(url)}">{html.escape(label)}</a>' for label, url in parts) + "</p>")

    def example(self, row: dict[str, str], index: int) -> None:
        score = fnum(row["object_score"])
        title = f"Пример {index}: {row['category_label']} — {row['object_id']} (D{row['document_id'][1:]}, стр. {row['page']}), {score}/100"
        crop = row["source_crop"]
        asset = row.get("prediction_asset", "")
        self.md.append(f"\n#### {title}\n\n![Исходный объект {row['object_id']}]({crop})\n")
        if asset:
            self.md.append(f"\nИзвлечённый visual asset: [{Path(asset).name}]({asset})\n")
        self.md.append("\n**Ground Truth**\n\n```text\n" + row["ground_truth"] + "\n```\n")
        self.md.append("\n**Output инструмента**\n\n```text\n" + row["tool_output"] + "\n```\n")
        self.md.append(f"\n**Тип ошибки:** {row['error_types']}\n\n**Причина:** {row['cause']}\n\n**Влияние на метрику:** {row['metric_impact']}\n")
        self.body.append(f'<details class="example"><summary>{html.escape(title)}</summary><div class="example-grid"><div><h4>Исходный объект</h4><img src="{html.escape(crop)}" alt="{html.escape(row["object_id"])}"></div><div><p><b>Тип ошибки:</b> {html.escape(row["error_types"])}</p><p><b>Причина:</b> {html.escape(row["cause"])}</p><p><b>Влияние:</b> {html.escape(row["metric_impact"])}</p></div></div>')
        if asset:
            self.body.append(f'<p><a href="{html.escape(asset)}">Извлечённый visual asset</a></p>')
        self.body.append("<h4>Ground Truth</h4><pre>" + html.escape(row["ground_truth"]) + "</pre><h4>Output инструмента</h4><pre>" + html.escape(row["tool_output"]) + "</pre></details>")

    def write(self) -> None:
        title = "Сравнительное исследование средств анализа PDF и преобразования в текст"
        subtitle = f"Полный технический отчёт · эксперимент {self.experiment_id} · 10 инструментов · 5 PDF · 40 GT-объектов"
        md_intro = f"# {title}\n\n{subtitle}\n\n"
        (self.output / "report.md").write_text(md_intro + "\n".join(self.md), encoding="utf-8")
        nav = "".join(f'<a href="#{anchor}">{html.escape(label)}</a>' for anchor, label in self.toc)
        css = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#1c3345;font:16px/1.62 system-ui,Segoe UI,sans-serif}header{background:#15394a;color:#fff;padding:48px max(24px,calc((100vw - 1250px)/2));border-bottom:6px solid #2eb7ac}header h1{max-width:1050px;font-size:35px;line-height:1.2;margin:0 0 12px}header p{color:#d5e4e9;margin:0}main{max-width:1280px;margin:24px auto;padding:38px;background:#fff;border:1px solid #dae3e8;border-radius:12px}nav{display:flex;flex-wrap:wrap;gap:8px 18px;padding:18px;background:#edf5f6;border-radius:8px;font-size:13px}nav a{width:calc(25% - 18px)}a{color:#116c7d;text-underline-offset:3px}h2{margin:52px 0 18px;padding-bottom:10px;border-bottom:1px solid #d8e2e7;font-size:26px}h3{margin-top:34px}p,li{max-width:1120px}.table-wrap{overflow:auto;margin:20px 0 30px}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.4}caption{text-align:left;font-weight:700;padding:0 0 10px}th,td{padding:9px 10px;border-bottom:1px solid #dbe4e8;vertical-align:top;text-align:left}th{background:#eaf1f4;white-space:nowrap}tbody tr:nth-child(even){background:#f7f9fa}figure{margin:28px 0 42px;padding:14px;border:1px solid #dfe7eb;border-radius:8px}figure img{display:block;width:100%;height:auto}figcaption{font-size:13px;color:#536779;margin-top:12px}.formula{margin:18px 0;padding:16px;background:#f4f7f8;border-left:4px solid #2eb7ac;overflow:auto}.formula code{font-size:15px;white-space:pre-wrap}.example{margin:14px 0;border:1px solid #dbe4e8;border-radius:8px;background:#fbfcfc}.example summary{padding:14px 16px;font-weight:700;cursor:pointer}.example>div,.example>p,.example>h4,.example>pre{margin-left:16px;margin-right:16px}.example-grid{display:grid;grid-template-columns:minmax(260px,40%) 1fr;gap:20px}.example img{max-width:100%;max-height:520px;object-fit:contain;border:1px solid #dbe4e8}.example pre{max-height:420px;overflow:auto;padding:13px;background:#17232d;color:#e9f1f4;white-space:pre-wrap;font:12px/1.45 Consolas,monospace}.links{font-size:14px}footer{margin-top:48px;padding-top:18px;border-top:1px solid #dbe4e8;color:#536779;font-size:13px}@media(max-width:800px){main{margin:10px;padding:18px}header{padding:28px 22px}nav a{width:100%}.example-grid{grid-template-columns:1fr}}@media print{body{background:#fff}header{padding:16px;background:#fff;color:#1c3345}header p{color:#536779}main{border:0;margin:0;padding:0;max-width:none}nav{display:none}figure,.table-wrap{break-inside:avoid}.example{break-before:page}.example[open] pre{max-height:none}h2{break-after:avoid}}
"""
        page = '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + f"<title>{html.escape(title)}</title><style>{css}</style></head><body>" + f"<header><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></header><main><nav>{nav}</nav>" + "\n".join(self.body) + '<footer>Отчёт построен из зафиксированного baseline без изменения PDF, Ground Truth и оценок. Машиночитаемые таблицы, графики, примеры и манифест находятся рядом с этим файлом.</footer></main></body></html>'
        (self.output / "report.html").write_text(page, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in args.experiment_id):
        raise ValueError("Invalid experiment ID")

    experiment_id = args.experiment_id
    p13 = ROOT / "reports" / "statistical_analysis" / experiment_id
    p14 = ROOT / "reports" / "error_analysis" / experiment_id
    output = ROOT / "reports" / "final_report" / experiment_id
    output.mkdir(parents=True, exist_ok=True)
    (output / "tables").mkdir(exist_ok=True)

    for directory in (p13 / "figures",):
        shutil.copytree(directory, output / directory.name, dirs_exist_ok=True)
    shutil.copytree(p13 / "tables", output / "tables", dirs_exist_ok=True)

    scores = read_csv(p13 / "tables" / "tool_scores.csv")
    versions = read_csv(p13 / "tables" / "versions.csv")
    coverage = read_csv(p13 / "tables" / "ground_truth_coverage.csv")
    groups = read_csv(p13 / "tables" / "group_scores.csv")
    difficulty = read_csv(p13 / "tables" / "category_difficulty.csv")
    chemistry = read_csv(p13 / "tables" / "chemistry_detection_extraction.csv")
    doc_difficulty = read_csv(p13 / "tables" / "document_difficulty.csv")
    all_examples = read_csv(p14 / "examples.csv")

    by_tool = {row["tool"]: row for row in scores}
    by_version = {row["tool"]: row for row in versions}
    examples_by_tool: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in all_examples:
        examples_by_tool[row["tool"]].append(row)
    selected: list[dict[str, str]] = []
    category_rank = {category: index for index, category in enumerate(CATEGORIES)}
    for tool in TOOLS:
        chosen = sorted(
            examples_by_tool[tool],
            key=lambda row: (float(row["object_score"]), category_rank[row["category"]], row["object_id"]),
        )[:5]
        selected.extend(chosen)
    write_csv(output / "examples_selected.csv", selected)
    (output / "examples_selected.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for folder_name in ("source_crops", "prediction_assets"):
        needed = {row[field] for row in selected for field in (("source_crop",) if folder_name == "source_crops" else ("prediction_asset",)) if row.get(field)}
        for relative in needed:
            source = p14 / relative
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    report = Report(output, experiment_id)

    report.heading(1, "introduction", "Введение")
    report.paragraph("Технический PDF хранит не только последовательность символов. Значение документа определяется совместным расположением абзацев, колонок, таблиц, математических и химических формул, изображений, подписей и связей внутри диаграмм. Поэтому преобразование PDF в TXT в данном исследовании трактуется как извлечение текстового содержания и восстановление структурированных объектов, которые затем могут быть представлены в текстовой или машиночитаемой форме.")
    report.paragraph("Исследование сравнивает пять локальных библиотек и пять облачных сервисов на одном фиксированном корпусе. Основной результат — воспроизводимая система количественных оценок по шести категориям, дополненная измерением скорости, памяти, стоимости и подробным анализом ошибок.")

    report.heading(2, "relevance", "Актуальность")
    report.paragraph("PDF остаётся основным форматом публикации научных статей, руководств и отчётов, однако его внутренняя структура часто ориентирована на визуальное отображение, а не на последовательное чтение программой. Ошибка порядка колонок меняет смысл текста; потеря строки или merged-cell искажает таблицу; плоская OCR-строка не сохраняет структуру дроби; пропуск подписи лишает изображение контекста. Эти дефекты непосредственно влияют на поиск, индексацию, RAG, анализ данных и архивную конвертацию.")
    report.paragraph("Практическая актуальность состоит в выборе инструмента под конкретный состав документов. Универсальный Overall полезен как сводный индикатор, но не заменяет профиль по категориям и эксплуатационные ограничения.")

    report.heading(3, "goal", "Цель и задачи")
    report.paragraph("Цель — экспериментально оценить качество и эксплуатационные характеристики десяти средств анализа технических PDF при преобразовании содержимого в текстовое и структурированное представление.")
    report.bullets([
        "сформировать фиксированный корпус из пяти технических PDF и Ground Truth по шести категориям;",
        "привести ответы десяти инструментов к единой схеме и выполнить однозначное сопоставление с GT;",
        "определить объектные, документные, категориальные и итоговую оценки;",
        "сравнить локальные/облачные и classic/ML группы;",
        "измерить время, RAM, VRAM и зарегистрированную API-стоимость;",
        "оценить зависимость результатов от документа и неопределённость;",
        "провести error analysis и представить пять проверяемых примеров на каждый инструмент.",
    ])

    report.heading(4, "object_subject", "Объект и предмет исследования")
    report.paragraph("Объект исследования — программные библиотеки и облачные сервисы анализа PDF. Предмет исследования — точность извлечения текста, таблиц, математических формул, химических формул и структур, изображений и диаграмм, а также скорость, потребление памяти и стоимость обработки.")
    report.paragraph("Единицей качества служит размеченный объект в PDF. Статистическим кластером для межинструментного анализа служит документ: 400 строк объектных результатов представляют 40 одинаковых GT-объектов, оценённых десятью инструментами, и не являются 400 независимыми наблюдениями.")

    report.heading(5, "services", "Обзор выбранных сервисов")
    service_rows = []
    for tool in TOOLS[5:]:
        row = by_version[tool]
        service_rows.append({"name": by_tool[tool]["name"], "version": row["installed_version"], "mode": json.loads(row["model_versions"] or "{}").get("product", json.loads(row["model_versions"] or "{}").get("ocr_engine", "—")), "note": TOOL_NOTES[tool], "docs": DOCS[tool]})
    report.table(service_rows, [("name", "Сервис"), ("version", "SDK/adapter"), ("mode", "Продукт/режим"), ("note", "Роль в benchmark"), ("docs", "Официальная документация")], "Пять облачных сервисов")
    report.link_paragraph([(by_tool[tool]["name"], DOCS[tool]) for tool in TOOLS[5:]])
    report.paragraph("Сравниваются конкретные API-режимы. Например, Mindee использован как Raw Text OCR, поэтому отсутствие типизированных таблиц или изображений характеризует выбранный endpoint, а не все продукты Mindee. Аналогично результаты LlamaParse относятся к tier agentic и сохранённой версии API.")

    report.heading(6, "libraries", "Обзор выбранных библиотек")
    local_rows = []
    for tool in TOOLS[:5]:
        row = by_version[tool]
        local_rows.append({"name": by_tool[tool]["name"], "version": row["installed_version"], "class": "ML" if row["technology"] == "ml_local" else "classic", "note": TOOL_NOTES[tool], "docs": DOCS[tool]})
    report.table(local_rows, [("name", "Библиотека"), ("version", "Версия"), ("class", "Класс"), ("note", "Роль в benchmark"), ("docs", "Официальная документация")], "Пять локальных библиотек")
    report.link_paragraph([(by_tool[tool]["name"], DOCS[tool]) for tool in TOOLS[:5]])
    report.paragraph("PyMuPDF, pdfplumber и pdfminer.six образуют группу классических локальных парсеров. Docling и MinerU образуют локальную ML-группу. Облачные сервисы не включались в сравнение classic/ML, поскольку внутренняя архитектура поставщика не всегда раскрыта и место исполнения является другой осью классификации.")

    report.heading(7, "dataset", "Описание тестового набора PDF")
    doc_rows = []
    cov = {row["document_id"]: row for row in coverage}
    for doc_id, (filename, pages, topic) in DOCUMENTS.items():
        row = {"id": doc_id, "file": filename, "pages": pages, "topic": topic}
        row.update({category: cov[doc_id][category] for category in CATEGORIES})
        row["total"] = cov[doc_id]["total"]
        doc_rows.append(row)
    report.table(doc_rows, [("id", "PDF"), ("file", "Файл"), ("pages", "Стр."), ("topic", "Содержание")] + [(category, CATEGORY_LABELS[category]) for category in CATEGORIES] + [("total", "GT")], "Корпус: 5 PDF, 98 страниц, 40 GT-объектов")
    report.paragraph("Корпус целенаправленно включает born-digital и сканированный материал, русский и английский текст, многоколоночную вёрстку, крупные таблицы, LaTeX-подобную математику, химические формулы и структуры, фотографии, графики и схемы. Категории распределены неравномерно: химия размечена только в D02, изображения — в D01 и D05, математика — в D01, D04 и D05.")

    report.heading(8, "methodology", "Методология benchmark")
    report.paragraph("Общая цепочка: PDF → нативный ответ инструмента → standardized schema → нормализация → one-to-one matching → объектные метрики → document macro → category macro → Overall. Стандартизация отделяет возможности поставщика от формата его ответа, а сохранение raw и provenance позволяет проверить каждое преобразование.")
    report.paragraph("Основной механизм сопоставления — венгерский алгоритм с отношением один к одному. Для объектов с координатами минимальный spatial IoU равен 0,10. Контентный fallback для текста, формул и таблиц без bbox требует similarity не ниже 0,25. Для фрагментированного текста разрешена GT-независимая сборка блоков с overlap относительно меньшего прямоугольника не ниже 0,50. Повторяющиеся слова в разных координатах сохраняются; блоки удаляются как дубликаты только при совпадении нормализованного текста и bbox.")
    report.paragraph("Для визуальных объектов используется sampled-GT recall: GT содержит выбранные примеры, а не исчерпывающую разметку всех картинок страницы. Поэтому несопоставленные кандидаты не объявляются false positive без полной аннотации страницы. Пропуск обязательного GT-объекта получает нулевой Object Score.")

    report.heading(9, "ground_truth", "Ground Truth")
    report.paragraph("Ground Truth состоит из 40 вручную проверенных объектов с фиксированными ID, страницами, bbox и эталонными payload. Размечены 9 текстовых регионов, 7 таблиц, 6 математических формул, 6 химических объектов, 5 изображений и 7 диаграмм. Табличный GT хранит строки, столбцы, spans, headers и текст ячеек; математический — LaTeX; химический — формулу либо структурное изображение с метками; визуальный — bbox, подпись и семантические элементы.")
    report.paragraph("GT нужен для количественного сравнения: без эталона нельзя измерить потерю символов, структуры или связей. Он не используется для создания ответа инструмента. Все подтверждённые исправления GT выполнены до текущего baseline по методологическим причинам; object IDs, категории и манифест сохранены. Во время Промптов 13–15 GT не изменялся.")

    report.heading(10, "metrics", "Количественная система оценки")
    report.paragraph("Все объектные оценки переводятся в диапазон 0–100. Если компонент метрики неприменим по определению GT, его вес исключается и оставшиеся веса нормируются. Если инструмент не выдал обязательный объект, применимые компоненты получают нулевое значение.")
    report.formula(r"S_text=100[0.40(1-CER)+0.30(1-WER)+0.10E+0.20R]", "CER и WER ограничиваются снизу нулевой quality-функцией; E — нормализованное edit similarity, R — сохранение reading order.")
    report.formula(r"S_table=100[0.30F_{cell}+0.25C_{cell}+0.20S_{struct}+0.25TEDS_{like}]", "Структура объединяет сходство числа строк, столбцов и F1 merged-cells; TEDS-like сравнивает линеаризованное дерево таблицы.")
    report.formula(r"S_math=100[0.05EM_{raw}+0.35EM_{norm}+0.40F1_{token}+0.20E]", "Нормализация LaTeX устраняет только допустимые поверхностные различия; математическая эквивалентность выражений отдельно не доказывается.")
    report.formula(r"S_{chem,linear}=100[0.35EM_{norm}+0.30F1_{token}+0.20E+0.15C_{composition}]", "Composition similarity сравнивает количества химических элементов после строгого разбора формулы.")
    report.formula(r"S_{chem,structure}=100[0.30D+0.25IoU+0.25F1_{label}+0.20X]", "D — обнаружение, X — наличие структурированного извлечения. Detection и conditional Structured Extraction дополнительно публикуются раздельно.")
    report.formula(r"S_{image}=100[0.30D+0.25IoU+0.25X+0.20C_{caption}]", "Подпись объединяет token F1 и edit similarity; при отсутствии подписи в GT компонент неприменим.")
    report.formula(r"S_{diagram}=100[0.20D+0.15IoU+0.25F1_{text}+0.15C_{caption}+0.25K]", "K объединяет полноту ключевых элементов и число подрисунков.")
    report.formula(r"S_{c,t}=\frac{1}{|D_c|}\sum_{d\in D_c}\frac{1}{|O_{d,c}|}\sum_{o\in O_{d,c}}S_{t,o};\quad Overall_t=\frac{1}{6}\sum_c S_{c,t}", "Сначала объекты усредняются внутри документа, затем документы — внутри категории. Для химии линейные формулы и структуры предварительно получают равный вес подтипов.")
    report.paragraph("Доверительные интервалы категорий рассчитаны percentile bootstrap с 10 000 повторов и seed=42. Общий Overall CI не строился: при пересэмплировании всего пяти PDF отдельная категория может исчезнуть, а перенормирование весов изменило бы сам показатель.")

    report.heading(11, "experiment", "Организация эксперимента")
    report.paragraph(f"Официальный baseline: {experiment_id}. Он содержит 10 tool rows, 50 успешных tool×PDF пар, 400 object rows, 0 errors и 13 статистических warnings; status=clean означает структурную целостность, а не отсутствие методологических ограничений. Контрольный Промпт 12 прошёл 625/625 проверок.")
    report.paragraph("Текущий baseline получен кэшированной повторной стандартизацией сохранённых raw-ответов после подтверждённых исправлений адаптеров, нормализации, matching и GT. Все 50 пар использовали standardize_only; новых acquisition, облачных вызовов и повторных тяжёлых запусков Docling/MinerU не было. Измерения времени, RAM, VRAM и API cost взяты из исходных запусков, а не из повторного вычисления метрик.")
    version_rows = [{"name": by_tool[t]["name"], "version": by_version[t]["installed_version"], "deployment": "локально" if by_version[t]["deployment"] == "local" else "облако", "details": by_version[t]["model_versions"]} for t in TOOLS]
    report.table(version_rows, [("name", "Инструмент"), ("version", "Версия"), ("deployment", "Развёртывание"), ("details", "Модели/режим")], "Зафиксированные версии и режимы")

    report.heading(12, "results", "Результаты")
    ordered = sorted(scores, key=lambda row: float(row["overall_score"]), reverse=True)
    quality_rows = [{"name": row["name"], **{category: fnum(row[f"{category}_score"]) for category in CATEGORIES}, "overall": fnum(row["overall_score"])} for row in ordered]
    report.table(quality_rows, [("name", "Инструмент")] + [(category, CATEGORY_LABELS[category]) for category in CATEGORIES] + [("overall", "Overall")], "Основные оценки качества, 0–100")
    report.figure("01_overall", "Overall quality", "Overall — равновесное среднее шести Category Scores; он не является универсальным рейтингом.")
    report.figure("04_heatmap", "Heatmap tool × category", "Профиль категорий показывает специализацию, скрытую сводным Overall.")
    report.figure("03_radar", "Radar charts", "Радиальные профили позволяют сопоставить сильные и слабые категории инструментов.")
    report.paragraph("Наибольший наблюдённый Overall у Docling — 59,44, затем MinerU — 56,69 и Nutrient — 52,14. Однако лидеры категорий различаются: PyMuPDF по тексту, MinerU по таблицам и изображениям, LlamaParse по математике, pdfminer.six по действующему Chemistry Score, Docling по диаграммам.")

    report.heading(13, "quality", "Сравнение качества")
    diff_rows = [{"category": CATEGORY_LABELS[row["category"]], "best": row["top_tools_at_0_01_precision"].replace(";", ", "), "score": fnum(row["best_score"]), "mean": fnum(row["mean_across_fixed_tools"]), "positive": f"{row['tools_with_nonzero_score']}/10", "matched": f"{row['matched_object_evaluations']}/{row['object_evaluations']}"} for row in difficulty]
    report.table(diff_rows, [("category", "Категория"), ("best", "Наибольшая оценка"), ("score", "Score"), ("mean", "Среднее 10 tools"), ("positive", "Score>0"), ("matched", "Matched")], "Трудность категорий и покрытие")
    report.paragraph("Текст оказался наиболее устойчивой категорией: среднее десяти инструментов 87,18. Таблицы имеют среднее 48,73 и особенно зависят от сохранения grid/merged cells. Математика имеет среднее 29,17; ненулевой результат получен у 9/10 инструментов, но структурный LaTeX остаётся сложным. Изображения имеют среднее 36,66. Наиболее трудны диаграммы: среднее 5,63, ненулевые результаты только у Docling и MinerU.")
    report.paragraph("Все инструменты обнаружили 6/6 химических GT-объектов по единому строгому GT-независимому правилу, поэтому Chemistry Detection=100 у всех. Conditional Structured Extraction различается от 39,04 до 75,13 и лучше отражает качество формул, bbox, labels и структурного представления. Chemistry Score и Overall при этом не заменялись диагностическими показателями.")
    chem_rows = [{"name": by_tool[row["tool"]]["name"], "score": fnum(row["chemistry_score"]), "detect": fnum(row["chemistry_detection_score"]), "extract": fnum(row["chemistry_structured_extraction_score"]), "objects": f"{row['detected_objects']}/{row['gt_objects']}"} for row in chemistry]
    report.table(chem_rows, [("name", "Инструмент"), ("score", "Chemistry"), ("detect", "Detection"), ("extract", "Extraction | detected"), ("objects", "Объекты")], "Химия: покрытие и качество payload раздельно")
    group_rows = [{"group": {"local": "Локальные", "cloud": "Облачные", "classic_local": "Classic local", "ml_local": "ML local"}[row["group"]], "text": fnum(row["text_score"]), "table": fnum(row["table_score"]), "math": fnum(row["math_score"]), "chemistry": fnum(row["chemistry_score"]), "image": fnum(row["image_score"]), "diagram": fnum(row["diagram_score"]), "overall": fnum(row["overall_score"])} for row in groups]
    report.table(group_rows, [("group", "Группа")] + [(category, CATEGORY_LABELS[category]) for category in CATEGORIES] + [("overall", "Overall")], "Средние фиксированных групп")
    report.paragraph("Средний Overall локальной группы равен 50,11, облачной — 41,85. Внутри локальной группы ML-системы имеют 58,07 против 44,81 у classic parsers. Средний Text почти одинаков, а различие связано главным образом с таблицами, математикой, изображениями и диаграммами. Это сравнение выбранных участников, а не всех решений соответствующего класса.")
    doc_rows = [{"doc": row["document_id"], "common": fnum(row["common_three_mean_across_fixed_tools"]), "text": fnum(row["text"]), "table": fnum(row["table"]), "diagram": fnum(row["diagram"])} for row in doc_difficulty]
    report.table(doc_rows, [("doc", "PDF"), ("common", "Среднее Text/Table/Diagram"), ("text", "Text"), ("table", "Table"), ("diagram", "Diagram")], "Сопоставимая трудность документов")
    report.paragraph("На общем наборе Text/Table/Diagram наиболее трудным оказался D05 — 34,18, наиболее лёгким D03 — 56,89. Зависимость от документа велика: например, Text pdfminer.six равен 100 на D02/D03, но 15,78 на D05. Поэтому один PDF не может представлять всё множество технических документов.")
    for fig, title, note in (
        ("02_categories_ci", "Категории и 95% CI", "Интервалы отражают малый фиксированный корпус; Math имеет 3 PDF, Image — 2, Chemistry — 1."),
        ("12_category_difficulty", "Трудность типов контента", "Средние десяти инструментов показывают резкое усложнение от текста к диаграммам."),
        ("13_groups", "Cloud/local и classic/ML", "Средние относятся к фиксированному составу групп."),
        ("10_document_text", "Text Score по PDF", "Текстовая устойчивость зависит от вёрстки и качества текстового слоя."),
        ("11_document_categories", "Категории по PDF", "Состав и трудность объектов неодинаковы между документами."),
        ("14_object_distributions", "Распределения объектных оценок", "Нулевая дисперсия нулевых оценок не означает надёжность; coverage анализируется отдельно."),
        ("15_document_sensitivity", "Чувствительность к исключению PDF", "Leave-one-document-out не заменяет официальный результат по полному корпусу."),
        ("16_paired_differences", "Парные различия", "Парный bootstrap сохраняет общие документы; поправка на множественные сравнения не применялась."),
    ):
        report.figure(fig, title, note)

    report.heading(14, "performance", "Сравнение производительности")
    performance_rows = [{"name": row["name"], "sec": fnum(row["sec_per_page"], 3), "total": fnum(row["total_processing_time_sec"], 1), "ram": fnum(float(row["peak_ram_mb"]) / 1024, 2), "vram": fnum(float(row["peak_vram_mb"]) / 1024, 2), "overall": fnum(row["overall_score"])} for row in sorted(scores, key=lambda row: float(row["sec_per_page"]))]
    report.table(performance_rows, [("name", "Инструмент"), ("sec", "с/стр."), ("total", "Всего, с"), ("ram", "Peak RAM, GiB"), ("vram", "Peak VRAM, GiB"), ("overall", "Overall")], "Скорость и ресурсы на 98 страницах")
    report.paragraph("Самый быстрый наблюдённый результат у pdfplumber — 0,086 с/стр., затем PyMuPDF — 0,357 с/стр. Docling требует 72,44 с/стр., около 10,62 GiB RAM и 7,99 GiB VRAM; MinerU — 2,70 с/стр., 5,66 GiB RAM и 4,66 GiB VRAM. Для облачных сервисов измерены клиентские ресурсы; серверные RAM/VRAM неизвестны и не интерпретируются как ноль поставщика.")
    report.paragraph("Опубликованный peak_vram_mb использует process GPU peak, если он доступен; иначе device-wide usage учитывается только при подтверждённой CUDA-активности адаптера. torch_peak_reserved не выдаётся за физическое потребление VRAM.")
    report.figure("05_speed", "Скорость", "Время повторного вычисления метрик исключено; показаны исходные замеры адаптеров.")
    report.figure("17_speed_by_document", "Скорость по документам", "Отдельные PDF могут доминировать во времени конкретного инструмента.")
    report.figure("06_resources", "RAM и VRAM", "Облачные значения отражают только клиентский процесс.")
    report.figure("08_quality_speed", "Качество и скорость", "Pareto-профиль зависит от требуемой категории и допустимых ресурсов.")

    report.heading(15, "cost", "Сравнение стоимости")
    cost_rows = [{"name": row["name"], "cost": fnum(row["api_cost_usd"]), "basis": row["api_cost_basis"], "overall": fnum(row["overall_score"])} for row in scores]
    report.table(cost_rows, [("name", "Инструмент"), ("cost", "Зафиксировано, USD"), ("basis", "Основание"), ("overall", "Overall")], "Стоимость, сохранённая в эксперименте")
    report.paragraph("Во всех строках сохранено 0,00 USD. Для локальных библиотек это not_applicable_local; для облачных сервисов — actual, estimated либо free/trial условия конкретного запуска. Эти значения не являются актуальным коммерческим прайс-листом и не позволяют ранжировать cost/quality. Корректное коммерческое сравнение потребует единого тарифа, объёма страниц, региона, налогов и даты фиксации цен.")
    report.figure("07_cost", "Зафиксированная стоимость", "Нули отражают условия запусков, а не доказанную бесплатность промышленной эксплуатации.")
    report.figure("09_quality_cost", "Качество и стоимость", "При одинаковом нулевом наблюдении Pareto-рейтинг стоимости неинформативен.")

    report.heading(16, "errors", "Error analysis")
    error_rows = [{"name": by_tool[tool]["name"], "errors": ERROR_SUMMARY[tool]} for tool in TOOLS]
    report.table(error_rows, [("name", "Инструмент"), ("errors", "Характерные ошибки на выбранных объектах")], "Характерные ошибки десяти инструментов")
    report.paragraph("Ошибки разделены на отсутствие объекта и низкое качество уже найденного объекта. Дополнительно проверялся nearby output другого типа: таблица могла присутствовать как TextBlock, формула Adobe — как Figure, диаграмма — как generic Image или Mermaid-текст. Такое содержимое не считается полноценным структурированным извлечением требуемой категории, но учитывается при объяснении причины.")
    report.link_paragraph([("Полный error analysis из 60 случаев", "../../error_analysis/" + experiment_id + "/report.html"), ("Все 60 примеров JSON", "../../error_analysis/" + experiment_id + "/examples.json")])

    report.heading(17, "examples", "По 5 примеров для каждого из 10 инструментов")
    report.paragraph("Для каждого инструмента взяты пять наименьших Object Score среди шести стратифицированных категориальных случаев Промпта 14; при равенстве использован фиксированный порядок категорий и object ID. Это диагностическая выборка, а не новая средняя оценка. Шестой случай каждого инструмента доступен в полном error analysis.")
    selected_map: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in selected:
        selected_map[row["tool"]].append(row)
    for tool in TOOLS:
        report.subheading(by_tool[tool]["name"])
        for index, row in enumerate(selected_map[tool], 1):
            report.example(row, index)

    report.heading(18, "synthesis", "Обобщение результатов")
    synthesis_rows = [
        {"need": "Максимально точный обычный текст", "tools": "PyMuPDF; затем Nutrient, Mindee, LlamaParse", "tradeoff": "PyMuPDF не восстанавливает сложную семантику таблиц/диаграмм автоматически."},
        {"need": "Таблицы", "tools": "MinerU, LlamaParse, Nutrient", "tradeoff": "Результаты близки и имеют широкую междокументную неопределённость."},
        {"need": "Математические формулы", "tools": "LlamaParse, MinerU, OCR.Space", "tradeoff": "Даже лидер имеет Math 49,33; обязательна проверка LaTeX."},
        {"need": "Химия", "tools": "Все обнаруживают 6/6; payload лучше сравнивать по Extraction", "tradeoff": "Химия проверена только на одном PDF."},
        {"need": "Изображения", "tools": "MinerU; далее Adobe Extract/PyMuPDF/pdfminer.six", "tradeoff": "Подписи, crop и типизация часто неполны."},
        {"need": "Диаграммы", "tools": "Docling; затем MinerU", "tradeoff": "Среднее категории 5,63; полноценное восстановление связей остаётся нерешённым."},
        {"need": "Минимальная локальная задержка", "tools": "pdfplumber или PyMuPDF", "tradeoff": "Структурные категории слабее локальных ML-систем."},
        {"need": "Локальный комплексный профиль", "tools": "Docling или MinerU", "tradeoff": "Docling ресурсоёмок; MinerU слабее по диаграммам."},
    ]
    report.table(synthesis_rows, [("need", "Задача"), ("tools", "Предпочтительный профиль"), ("tradeoff", "Ограничение")], "Практический выбор по специализации")
    report.paragraph("Ни один инструмент не доминирует во всех категориях. Лучший выбор определяется долей обычного текста, таблиц, формул и визуальных объектов, требованиями к локальности, задержке и ресурсам. Для промышленной системы разумна маршрутизация: быстрый локальный парсер для born-digital текста и более тяжёлый структурный/OCR pipeline для сложных страниц.")

    report.heading(19, "limitations", "Ограничения исследования")
    report.bullets([
        "Корпус мал: пять целенаправленно выбранных PDF и 40 репрезентативных, а не исчерпывающе размеченных объектов.",
        "Химия представлена одним документом, изображения двумя, математика тремя; переносимость соответствующих результатов ограничена.",
        "Сравниваются конкретные версии, адаптеры и режимы; иной endpoint, OCR engine или модель может дать другой профиль.",
        "Object Score измеряет совпадение с выбранным представлением GT; для математики не проверяется символическая эквивалентность, для диаграмм — полная графовая эквивалентность.",
        "Reading order размечен косвенно внутри выбранных регионов; отдельного полного порядка всех блоков страницы нет.",
        "Облачное время включает клиентскую и сетевую часть конкретных запусков, но не раскрывает серверные ресурсы; локальное и облачное время различаются по природе.",
        "Стоимость зафиксирована как нулевая в условиях запусков и не является коммерческим сравнением тарифов.",
        "13 статистических предупреждений сохранены как наблюдения; outliers автоматически не удалялись.",
    ])

    report.heading(20, "conclusions", "Выводы")
    report.bullets([
        "Предложенная система даёт отдельные воспроизводимые оценки текста, таблиц, математики, химии, изображений и диаграмм, а затем равновесный Overall.",
        "PyMuPDF наиболее точен по обычному тексту (99,98), MinerU — по таблицам (71,44) и изображениям (75,01), LlamaParse — по математике (49,33), pdfminer.six — по действующему Chemistry Score (82,59), Docling — по диаграммам (42,19).",
        "Наибольший Overall у Docling (59,44), но он не является лучшим по всем категориям и требует наибольших ресурсов и времени.",
        "Локальные ML-системы превосходят классические локальные парсеры в среднем Overall за счёт структурных и визуальных категорий; по тексту средние групп почти совпадают.",
        "Наиболее сложны диаграммы и математика. Таблицы и изображения также требуют структурного, а не только текстового извлечения.",
        "Результаты существенно зависят от PDF: D05 сложнее D03 на общем наборе Text/Table/Diagram, а отдельные инструменты резко меняют качество и время между документами.",
        "Повышение score оправдано только при исправлении ошибок адаптера, matching, нормализации или GT. Подгонка правил под конкретный GT снизила бы исследовательскую достоверность.",
        "Для внешнего сервера следует предоставлять профиль по категориям, время, ресурсы и примеры ошибок, а не один рейтинг Overall.",
    ])
    report.link_paragraph([("Промпт 12 — контроль baseline", "../../PROMPT12_CONTROL.md"), ("Промпт 13 — статистический анализ", "../../PROMPT13_ANALYSIS.md"), ("Промпт 14 — error analysis", "../../PROMPT14_ERROR_ANALYSIS.md")])
    report.write()

    references = [{"tool": by_tool[tool]["name"], "url": DOCS[tool], "accessed": "2026-10-04"} for tool in TOOLS]
    write_csv(output / "official_documentation.csv", references)

    inputs = [
        p13 / "analysis_manifest.json", p13 / "tables" / "tool_scores.csv",
        p13 / "tables" / "versions.csv", p14 / "analysis_manifest.json",
        p14 / "examples.csv", ROOT / "config" / "metrics.yaml",
        ROOT / "config" / "matching.yaml", ROOT / "benchmark" / "object_manifest.json",
        ROOT / "ground_truth" / "objects.jsonl", Path(__file__),
    ]
    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": experiment_id,
        "status": "complete",
        "report_sections": 20,
        "tools": 10,
        "services": 5,
        "libraries": 5,
        "documents": 5,
        "pages": 98,
        "ground_truth_objects": 40,
        "object_evaluations": 400,
        "selected_examples": len(selected),
        "examples_per_tool": 5,
        "figures": len(list((output / "figures").glob("*.png"))),
        "official_documentation_links": len(references),
        "inputs_changed": [],
        "adapter_executions": 0,
        "network_api_calls": 0,
        "selection_rule": "five lowest Prompt 14 category-stratified Object Scores per tool; ties by fixed category order then object_id",
        "input_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in inputs},
    }
    (output / "analysis_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    entry = f"""# Промпт 15 — полный технический отчёт

Эксперимент: `{experiment_id}`. Подготовлен отчёт из 20 разделов по пяти локальным библиотекам и пяти облачным сервисам, 5 PDF, 98 страницам и 40 GT-объектам.

- [Полный HTML-отчёт](final_report/{experiment_id}/report.html)
- [Полный Markdown-отчёт](final_report/{experiment_id}/report.md)
- [50 примеров в CSV](final_report/{experiment_id}/examples_selected.csv)
- [50 примеров в JSON](final_report/{experiment_id}/examples_selected.json)
- [Официальная документация](final_report/{experiment_id}/official_documentation.csv)
- [Манифест](final_report/{experiment_id}/analysis_manifest.json)

Отчёт использует официальный baseline, графики Промпта 13 и проверенные примеры Промпта 14. PDF, Ground Truth, object manifest, кэш и экспериментальные оценки не изменялись. Новых запусков адаптеров и облачных API не было.
"""
    (ROOT / "reports" / "PROMPT15_FULL_REPORT.md").write_text(entry, encoding="utf-8")
    print(json.dumps({"status": "complete", "output": str(output), "sections": 20, "examples": len(selected), "figures": manifest["figures"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
