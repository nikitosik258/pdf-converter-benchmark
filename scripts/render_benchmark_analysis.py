"""Russian technical report in Markdown and self-contained offline HTML."""
from __future__ import annotations

import html
import re
from statistics import mean, median


def fmt(value, digits=2):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return f"{value:.{digits}f}".replace(".", ",")
    return str(value)


class Report:
    def __init__(self, ctx, figures):
        self.ctx=ctx; self.figures={r["name"]:r for r in figures}
        self.html=[]; self.md=[]; self.toc=[]

    def heading(self, anchor, title):
        self.toc.append((anchor,title)); self.html.append(f'<h2 id="{anchor}">{html.escape(title)}</h2>')
        self.md.append("\n## "+title+"\n")

    def p(self, text):
        self.html.append("<p>"+html.escape(text)+"</p>"); self.md.append(text+"\n")

    def table(self, rows, columns, caption, filename=None):
        self.html.append('<div class="table-wrap"><table><caption>'+html.escape(caption)+"</caption><thead><tr>"+
            "".join("<th>"+html.escape(label)+"</th>" for _,label in columns)+"</tr></thead><tbody>")
        self.md.extend([caption+"\n","| "+" | ".join(label for _,label in columns)+" |","| "+" | ".join("---" for _ in columns)+" |"])
        for row in rows:
            values=[fmt(row.get(key)) for key,_ in columns]
            self.html.append("<tr>"+"".join("<td>"+html.escape(v)+"</td>" for v in values)+"</tr>")
            self.md.append("| "+" | ".join(v.replace("|","\\|") for v in values)+" |")
        self.html.append("</tbody></table></div>"); self.md.append("")
        if filename:
            self.html.append(f'<p class="download"><a href="tables/{filename}.csv">Полная таблица CSV</a></p>')
            self.md.append(f"[Полная таблица CSV](tables/{filename}.csv)\n")

    def figure(self,name):
        figure=self.figures[name]
        svg=(self.ctx["out"]/figure["svg"]).read_text(encoding="utf-8")
        svg=svg[svg.index("<svg"):]
        # Separate Matplotlib SVGs reuse IDs such as figure_1 and patch_1.
        # Inline figures share a DOM, so prefix definitions AND references.
        svg=re.sub(r'\bid="([^"]+)"',lambda m:f'id="{name}-{m[1]}"',svg)
        svg=re.sub(r'url\(#([^)]+)\)',lambda m:f'url(#{name}-{m[1]})',svg)
        svg=re.sub(r'((?:xlink:)?href=")#([^"]+)"',lambda m:f'{m[1]}#{name}-{m[2]}"',svg)
        self.html.append('<figure aria-label="'+html.escape(figure["title"])+ '">'+svg+
            '<figcaption>'+html.escape(figure["note"])+f' <a href="{figure["svg"]}">SVG</a> · <a href="{figure["png"]}">PNG</a></figcaption></figure>')
        self.md.append(f'![{figure["title"]}]({figure["png"]})\n\n{figure["note"]}\n')

    def write(self):
        title="PDF benchmark · статистический анализ"
        subtitle=f'Промт 13 · эксперимент {self.ctx["experiment_id"]} · 10 инструментов · 5 PDF · 40 GT'
        nav="".join(f'<a href="#{anchor}">{html.escape(label)}</a>' for anchor,label in self.toc)
        document='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PDF benchmark — промт 13</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#183045;font:16px/1.65 system-ui,Segoe UI,sans-serif}header{background:#15394a;color:white;padding:48px max(24px,calc((100vw - 1200px)/2));border-bottom:6px solid #2eb7ac}header h1{font-size:34px;line-height:1.2;margin:0 0 12px}header p{margin:0;color:#d0e2e8}main{max-width:1260px;margin:24px auto;padding:36px;background:white;border:1px solid #dce3e7;border-radius:12px}nav{display:flex;gap:10px 22px;flex-wrap:wrap;margin:0 0 32px;padding:20px;background:#edf5f6;border-radius:8px;font-size:14px}a{color:#14687c;text-underline-offset:3px}h2{margin:48px 0 16px;line-height:1.3;font-size:25px;border-bottom:1px solid #dce3e7;padding-bottom:12px}p{max-width:1100px}figure{margin:28px 0 40px;padding:12px;border:1px solid #e1e7eb;border-radius:8px;overflow:auto}figure svg{display:block;width:100%;height:auto;min-width:640px}figcaption{font-size:13px;color:#536779;margin-top:16px}.table-wrap{overflow:auto;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.45}caption{text-align:left;font-weight:650;padding:0 0 10px}th,td{padding:10px 12px;border-bottom:1px solid #dce3e7;text-align:right;vertical-align:top}th:first-child,td:first-child{text-align:left}th{background:#eaf1f4;white-space:nowrap}tbody tr:nth-child(even){background:#f7f9fa}.download{font-size:13px;margin-top:-8px}footer{font-size:13px;color:#536779;border-top:1px solid #dce3e7;margin-top:40px;padding-top:18px}@media(max-width:700px){main{padding:18px;margin:12px}header{padding:28px 24px}header h1{font-size:27px}}@media print{body{background:white}main{border:0;margin:0;max-width:none;padding:0}nav,.download{display:none}figure{break-inside:avoid}h2{break-after:avoid}table{font-size:10px}header{padding:16px;background:white;color:#183045}header p{color:#536779}figure svg{min-width:0}}
</style></head><body>'''
        document+=f'<header><h1>{title}</h1><p>{html.escape(subtitle)}</p></header><main><nav>{nav}</nav>'
        document+="\n".join(self.html)+"<footer>Отчёт работает без сети. Графики встроены в HTML; исходные PNG/SVG и CSV доступны рядом. Полный атлас: <a href=\"figures.pdf\">figures.pdf</a>. Происхождение данных и хэши: <a href=\"analysis_manifest.json\">analysis_manifest.json</a>.</footer></main></body></html>"
        (self.ctx["out"]/"report.html").write_text(document,encoding="utf-8")
        (self.ctx["out"]/"report.md").write_text("# "+title+"\n\n"+subtitle+"\n"+"\n".join(self.md),encoding="utf-8")


def write_reports(ctx,figures):
    report=Report(ctx,figures); table=report.table; p=report.p
    data=ctx["tables"]; by=ctx["by_tool"]; names=ctx["tool_names"]; cats=ctx["categories"]; labels=ctx["category_labels"]
    ordered=sorted(by,key=lambda t:by[t]["overall_score"],reverse=True)
    ci={(r["tool"],r["category"]):r for r in data["category_uncertainty"]}
    var={(r["tool"],r["category"]):r for r in data["object_variance"]}
    depend={(r["tool"],r["category"]):r for r in data["document_dependence"]}
    speeds={r["tool"]:r for r in data["speed_sensitivity"]}
    groups={r["group"]:r for r in data["group_scores"]}
    leaders={c:max(by,key=lambda t:by[t][c+"_score"]) for c in cats}
    math_positive=sorted(
        (tool for tool in by if by[tool]["math_score"] > 0),
        key=lambda tool:by[tool]["math_score"],
        reverse=True,
    )
    warning_count=len(data["quality_issues"])
    top_overall=ordered[0]
    runner_up=ordered[1]
    chemistry_best = by[leaders["chemistry"]]["chemistry_score"]
    chemistry_reporting = next(
        row for row in data["chemistry_detection_extraction"]
        if row["tool"] == leaders["chemistry"]
    )
    chemistry_object_counts = {
        tool: sum(
            row["chemistry"] for row in data["standardized_object_counts"]
            if row["tool"] == tool
        )
        for tool in by
    }
    chemistry_object_count = sum(chemistry_object_counts.values())
    chemistry_finding = (
        "У всех Chemistry Score=0 по строгой типизированной схеме benchmark."
        if chemistry_best == 0
        else (
            f"Наибольший Chemistry Score у {names[leaders['chemistry']]} "
            f"({fmt(chemistry_best)}); обнаружение и качество извлечения "
            "показаны отдельно ниже."
        )
    )

    report.heading("findings","1. Основные результаты")
    p(
        "В этом эксперименте нет инструмента, покрывающего все шесть категорий. "
        f"Наибольший Text Score у {names[leaders['text']]}, Table — у {names[leaders['table']]}, "
        f"Math — у {names[leaders['math']]}, "
        f"Image — у {names[leaders['image']]}, Diagram — у {names[leaders['diagram']]}. "
        + chemistry_finding
    )
    p(
        f"Наибольший наблюдённый Overall у {names[top_overall]} ({fmt(by[top_overall]['overall_score'])}), "
        f"второй — {names[runner_up]} ({fmt(by[runner_up]['overall_score'])}). "
        f"По отдельным категориям {names[leaders['diagram']]} специализируется на Diagram, "
        f"{names[leaders['table']]} — на Table и Image, а {names[leaders['math']]} — на Math. "
        f"При этом {names[runner_up]} требует "
        f"{fmt(by[runner_up]['sec_per_page'])} с/стр., а самый быстрый инструмент "
        f"{names[min(by,key=lambda t:by[t]['sec_per_page'])]} — {fmt(min(r['sec_per_page'] for r in by.values()),3)} с/стр. "
        "Поэтому Overall не заменяет анализ специализации, скорости и ресурсов."
    )
    p("Все выводы относятся к пяти фиксированным техническим PDF, конкретным адаптерам, версиям и режимам. Измеряется вся цепочка «инструмент → standardized schema → matching → evaluator». Оценка продукта во всех возможных конфигурациях из этого эксперимента не следует.")
    table([{"tool":names[t],**{c:by[t][c+"_score"] for c in cats},"overall":by[t]["overall_score"]} for t in ordered],
          [("tool","Инструмент")]+[(c,labels[c]) for c in cats]+[("overall","Overall")],"Основные оценки, 0–100","tool_scores")
    report.figure("01_overall"); report.figure("04_heatmap")

    report.heading("protocol","2. Данные, методика и статистическая единица")
    p(f"Источник: {ctx['experiment_id']}; это кэшированная re-standardization сохранённых raw-ответов после исправлений относительно {ctx['source_experiment_id']}. 10 tool rows, 50 успешных пар, 400 object rows; 0 ошибок, {warning_count} сохранённых предупреждений. Все утверждённые исправления Ground Truth уже входят в этот baseline; во время анализа PDF, GT, кэши и оценки не изменялись. Новых обращений к поставщикам и запусков парсеров при анализе нет.")
    table([{"document_id":r["document_id"],"filename":r["filename"],"pages":r["pages"]} for r in ctx["pdfs"]],[("document_id","PDF"),("filename","Файл"),("pages","Страниц")],"Корпус: 98 страниц")
    table(data["ground_truth_coverage"],[("document_id","PDF")]+[(c,labels[c]) for c in cats]+[("total","Всего GT")],"Матрица разметки; 0 здесь означает отсутствие GT категории","ground_truth_coverage")
    p("Category Score = среднее document scores среди документов, где категория размечена. Document score = среднее object scores; для химии сначала отдельно усредняются linear formulas и structures, затем эти два подтипа получают равный вес. Overall = сумма шести Category Scores / 6. Отсутствующее предсказание для GT даёт 0. Отсутствие самой GT-категории в PDF не даёт 0 и не добавляет документ в знаменатель категории.")
    p("400 строк — повторная оценка одних и тех же 40 объектов десятью инструментами. Их нельзя считать 400 независимыми наблюдениями. Для межинструментных сравнений пары сопоставляются на одних и тех же PDF; основной кластер — документ. Документы выбраны целенаправленно, поэтому интервалы ниже характеризуют ограниченный набор, а не случайную выборку всех технических PDF.")
    p("Объектные mean/median/variance в дополнительных таблицах — описательные micro-statistics. Они не заменяют canonical macro scores. Страница и document_overall_present_categories — диагностические уровни; их нельзя усреднить и назвать финальным Overall.")
    p(f"Сохранённые CI рассчитаны percentile bootstrap с 10 000 выборок и seed=42. Text/Table/Diagram имеют 5 PDF, Math — 3, Image — 2. Chemistry размещена только в D02: интервал лидера [{fmt(ci[leaders['chemistry'],'chemistry']['ci95_low'])}; {fmt(ci[leaders['chemistry'],'chemistry']['ci95_high'])}] отражает только bootstrap химических объектов внутри этого документа и не оценивает переносимость на новые документы. Для Overall общий CI не строится: bootstrap пяти PDF может потерять целую категорию; скрытое перенормирование весов изменило бы показатель.")
    p("Проверки при анализе независимо воспроизводят Category/Overall scores, суммы времени и стоимости, максимумы RAM/VRAM и уникальность ключей. Все читаемые входы снабжены SHA-256 и проверены на неизменность после анализа. Значения N/A в таблицах и графиках оставлены пропусками, а не нулями.")

    report.heading("categories","3. Сравнение по категориям и специализация")
    rows=[]
    for r in data["category_difficulty"]:
        leader_names="все: 0" if r["best_score"]==0 else ", ".join(names[t] for t in r["top_tools_at_0_01_precision"].split(";"))
        rows.append({"category":labels[r["category"]],"leader":leader_names,"best":r["best_score"],"mean":r["mean_across_fixed_tools"],"median":r["median_across_fixed_tools"],
                     "positive":f"{r['tools_with_nonzero_score']}/10","matched":f"{r['matched_object_evaluations']}/{r['object_evaluations']}"})
    table(rows,[("category","Категория"),("leader","Наибольшая оценка*"),("best","Score"),("mean","Среднее 10 tools"),("median","Медиана"),("positive","Score>0"),("matched","Matched / оценки")],"*Практическое равенство на точности 0,01 п.п.; это не тест превосходства","category_difficulty")
    table(
        [
            {
                **row,
                "tool": names[row["tool"]],
                "detected": f"{row['detected_objects']}/{row['gt_objects']}",
                "linear": f"{row['detected_linear_formulas']}/{row['gt_linear_formulas']}",
                "structures": f"{row['detected_structures']}/{row['gt_structures']}",
            }
            for row in data["chemistry_detection_extraction"]
        ],
        [
            ("tool", "Инструмент"),
            ("chemistry_score", "Chemistry Score"),
            ("chemistry_detection_score", "Detection"),
            ("chemistry_structured_extraction_score", "Extraction | detected"),
            ("detected", "Обнаружено"),
            ("linear", "Линейные"),
            ("structures", "Структуры"),
        ],
        "Химия: обнаружение отделено от качества структурированного извлечения",
        "chemistry_detection_extraction",
    )
    p(
        "Chemistry Detection Score использует все GT-объекты и макробалансирует "
        "линейные формулы и структуры. Extraction | detected оценивает формулу, "
        "координаты, текстовые метки и наличие извлечённого представления только "
        "среди сопоставленных объектов; при отсутствии обнаружений выводится N/A. "
        "Оба показателя диагностические: действующие Chemistry Score и Overall "
        "ими не заменяются и не пересчитываются."
    )
    p(f"Текст. PyMuPDF: {fmt(by['pymupdf']['text_score'])}, 95% CI [{fmt(ci['pymupdf','text']['ci95_low'])}; {fmt(ci['pymupdf','text']['ci95_high'])}], SD между PDF всего {fmt(ci['pymupdf','text']['std_dev'],3)}. Далее идут Nutrient ({fmt(by['nutrient']['text_score'])}), Mindee ({fmt(by['mindee']['text_score'])}), LlamaParse ({fmt(by['llamaparse']['text_score'])}), Adobe Extract ({fmt(by['adobe_extract']['text_score'])}) и Docling ({fmt(by['docling']['text_score'])}). Это результат исправленной пространственной сборки и гранулярности текстовых блоков; отдельной reading-order GT всё ещё нет.")
    p(f"Таблицы. Три близких результата: MinerU {fmt(by['mineru']['table_score'])}, LlamaParse {fmt(by['llamaparse']['table_score'])}, Nutrient {fmt(by['nutrient']['table_score'])}. Их междокументные CI широки: MinerU [{fmt(ci['mineru','table']['ci95_low'])}; {fmt(ci['mineru','table']['ci95_high'])}], LlamaParse [{fmt(ci['llamaparse','table']['ci95_low'])}; {fmt(ci['llamaparse','table']['ci95_high'])}], Nutrient [{fmt(ci['nutrient','table']['ci95_low'])}; {fmt(ci['nutrient','table']['ci95_high'])}]. Docling и Adobe Extract имеют около 66. Нельзя объявить устойчивого универсального лидера по разнице в сотые пункта на пяти PDF.")
    p(
        "Математика. Ненулевые результаты получили 9/10 инструментов: "
        + ", ".join(f"{names[tool]} ({fmt(by[tool]['math_score'])})" for tool in math_positive)
        + f". Лидер {names[leaders['math']]} имеет междокументный 95% CI "
        f"[{fmt(ci[leaders['math'],'math']['ci95_low'])}; {fmt(ci[leaders['math'],'math']['ci95_high'])}] "
        "на трёх PDF. Здесь оценивается совпадение LaTeX/токенов и качество сохранённого "
        "plain-text payload, а не доказанная математическая эквивалентность."
    )
    p(
        f"Химия. Общий GT-независимый слой сформировал {chemistry_object_count} ChemicalObject "
        "для всех десяти инструментов. Каждый инструмент сопоставил 6/6 GT-объектов, "
        "поэтому Detection=100 у всех и сам по себе этот показатель их не различает. "
        f"Наибольший Chemistry Score у {names[leaders['chemistry']]}: {fmt(chemistry_best)}, "
        f"условный Extraction={fmt(chemistry_reporting['chemistry_structured_extraction_score'])}. "
        "Различия Chemistry Score отражают качество формулы, координат, текстовых меток и "
        "извлечённого представления после одинакового правила обнаружения. В GT только один "
        "химический документ: 2 линейные формулы и 4 структуры, поэтому междокументную "
        "устойчивость оценить нельзя."
    )
    p(f"Изображения. MinerU лидирует с {fmt(by['mineru']['image_score'])} и CI [{fmt(ci['mineru','image']['ci95_low'])}; {fmt(ci['mineru','image']['ci95_high'])}]. Adobe Extract, PyMuPDF и pdfminer.six практически совпадают около {fmt(by['pymupdf']['image_score'])}; Docling — {fmt(by['docling']['image_score'])}, Nutrient — {fmt(by['nutrient']['image_score'])}. Image CI основан только на D01 и D05, поэтому устойчивость за пределами этих двух документов не установлена.")
    p(f"Диаграммы. Ненулевой score только у Docling ({fmt(by['docling']['diagram_score'])}) и MinerU ({fmt(by['mineru']['diagram_score'])}). Docling получает положительные document scores на всех пяти PDF и CI [{fmt(ci['docling','diagram']['ci95_low'])}; {fmt(ci['docling','diagram']['ci95_high'])}]. Это специализация текущей цепочки извлечения/классификации, а не гарантия понимания логики всех схем.")
    report.figure("02_categories_ci"); report.figure("03_radar"); report.figure("12_category_difficulty")

    report.heading("groups","4. Cloud/local и classic/ML")
    table([{"group":ctx["group_labels"][r["group"]],**{c:r[c+"_score"] for c in cats},"overall":r["overall_score"]} for r in data["group_scores"]],
          [("group","Группа")]+[(c,labels[c]) for c in cats]+[("overall","Mean Overall")],"Равновесные средние фиксированных инструментов","group_scores")
    p(f"Среднее Overall локальных участников — {fmt(groups['local']['overall_score'])}, облачных — {fmt(groups['cloud']['overall_score'])}. Эта разность относится именно к пяти выбранным реализациям каждой группы. Локальная группа включает быстрые classic parsers и тяжёлые ML-системы; переносить её среднее на «любую локальную библиотеку» нельзя.")
    p(f"Внутри local: ML (Docling, MinerU) имеет среднее Overall {fmt(groups['ml_local']['overall_score'])}, classic (PyMuPDF, pdfplumber, pdfminer.six) — {fmt(groups['classic_local']['overall_score'])}. Средний Text почти одинаков: ML {fmt(groups['ml_local']['text_score'])}, classic {fmt(groups['classic_local']['text_score'])}. Преимущество ML-группы в этом наборе связано с Table, Math, Image и Diagram, но достигается при намного больших медианных RAM/VRAM и времени.")
    p("Cloud/local — место исполнения, classic/ML — тип реализации. Облачные сервисы могут использовать ML, поэтому они не отнесены к «не-ML». Их внутренние модели не классифицировались без подтверждения. Парные интервалы разности групп пересэмплируют документы, сохраняя состав инструментов; они не учитывают неопределённость выбора продуктов.")
    table([{"comparison":ctx["group_labels"][r["left"]]+" − "+ctx["group_labels"][r["right"]],"category":labels[r["category"]],"n":r["n_documents"],"delta":r["delta"],"low":r["ci95_low"],"high":r["ci95_high"]} for r in data["paired_group_differences"]],
          [("comparison","Разность групп"),("category","Категория"),("n","PDF"),("delta","Δ, п.п."),("low","95% low"),("high","95% high")],"Исследовательские парные CI; состав инструментов фиксирован","paired_group_differences")
    report.figure("13_groups")

    report.heading("documents","5. Зависимость от конкретного PDF")
    table([{"tool":names[t],**{d:ctx["doc_scores"][t,d,"text"] for d in ctx["documents"]}} for t in ordered],[("tool","Инструмент")]+[(d,d) for d in ctx["documents"]],"Текст по документам: сопоставимый набор категорий","document_category_scores")
    report.figure("10_document_text"); report.figure("11_document_categories")
    p("Сырые document_overall_present_categories смешивают разные категории: D03 содержит text/table/diagram, D02 дополнительно chemistry, D01 и D05 — image и math. Поэтому для отдельного описательного сравнения PDF ниже используется один и тот же набор Text/Table/Diagram, размеченный во всех документах. Он не заменяет шестикатегорийный Overall.")
    table([{ "document":r["document_id"],"common":r["common_three_mean_across_fixed_tools"],"text":r["text"],"table":r["table"],"diagram":r["diagram"]} for r in data["document_difficulty"]],
          [("document","PDF"),("common","Среднее Text/Table/Diagram"),("text","Text"),("table","Table"),("diagram","Diagram")],"Средние по фиксированным 10 инструментам","document_difficulty")
    dd=sorted(data["document_difficulty"],key=lambda r:r["common_three_mean_across_fixed_tools"])
    p(f"При таком общем наборе категорий наименьшее среднее у {dd[0]['document_id']} ({fmt(dd[0]['common_three_mean_across_fixed_tools'])}), наибольшее у {dd[-1]['document_id']} ({fmt(dd[-1]['common_three_mean_across_fixed_tools'])}). Это эмпирическая трудность для выбранных адаптеров и GT; она не доказывает причинный эффект тематики, числа страниц или наличия колонок.")
    for t in ("pdfplumber","pymupdf","llamaparse"):
        r=depend[t,"text"]
        p(f"{names[t]} Text: минимум {fmt(r['min_score'])} на {r['min_document']}, максимум {fmt(r['max_score'])} на {r['max_document']}, размах {fmt(r['range'])} п.п. Полные min/max/SD по 60 комбинациям инструмент–категория приведены в document_dependence.csv.")
    lodo_summary=[]
    for t in ordered:
        rows=[r for r in data["leave_one_document_out"] if r["tool"]==t and r["overall_score"] is not None]
        low=min(rows,key=lambda r:r["overall_delta"]); high=max(rows,key=lambda r:r["overall_delta"])
        lodo_summary.append({"tool":names[t],"low":low["overall_delta"],"low_doc":low["omitted_document"],"high":high["overall_delta"],"high_doc":high["omitted_document"]})
    table(lodo_summary,[("tool","Инструмент"),("low","Min ΔOverall"),("low_doc","Без PDF"),("high","Max ΔOverall"),("high_doc","Без PDF")],"Leave-one-document-out: только варианты с сохранением всех категорий","leave_one_document_out")
    p("Исключение D02 делает Overall неопределённым: химия нигде больше не размечена. Такие строки оставлены N/A. При исключении остальных PDF шесть весов остаются равными; это проверка чувствительности, не очистка данных и не новая официальная оценка. Основной результат по-прежнему включает все пять документов.")
    report.figure("15_document_sensitivity")

    report.heading("variance","6. Объектный разброс, покрытие и доверительные интервалы")
    table([{"tool":names[t],"category":labels[c],"n":var[t,c]["n"],"matched":var[t,c]["matched"],"mean":var[t,c]["mean"],"sd":var[t,c]["sd"],"variance":var[t,c]["sample_variance"],
            "median":var[t,c]["median"],"min":var[t,c]["min"],"max":var[t,c]["max"]} for t in ordered for c in cats],
          [("tool","Инструмент"),("category","Категория"),("n","GT"),("matched","Matched"),("mean","Mean объектов"),("sd","SD"),("variance","Variance"),("median","Median"),("min","Min"),("max","Max")],"Sample variance: сумма квадратов отклонений / (n−1). Mean объектов — диагностический","object_variance")
    p("В object_variance.csv также есть Q1/Q3, количество нулей, средняя оценка только matched объектов и разложение SS_total = SS_within_document + SS_between_documents. Доля SS_between / SS_total показывает, сколько наблюдённого разброса связано с группировкой по PDF; это описательное разложение, не причинная доля объяснённой дисперсии. При одном объекте на PDF within-часть равна нулю по конструкции; при всех нулях доля не определена.")
    p("Низкая дисперсия при нулевых scores не означает надёжное распознавание. Отдельно нужно смотреть coverage: matched — наличие сопоставленного объекта, а не его правильность. Положительный score при широком разбросе указывает на зависимость результата от конкретных GT; большой matched_fraction при низком score — на проблемы содержимого/структуры уже найденных объектов.")
    report.figure("14_object_distributions")
    p("Для всех 45 пар инструментов и 6 категорий сохранены 270 строк paired_category_differences.csv. Разность считается на одинаковых document scores; bootstrap выбирает одни и те же индексы документов для обоих инструментов. Для Chemistry при n=1 парный междокументный CI оставлен пустым. Интервалы исследовательские, без поправки на множественные сравнения; на их основе не делаются 270 утверждений о статистическом превосходстве.")
    report.figure("16_paired_differences")
    pair_map={(r["left"],r["right"],r["category"]):r for r in data["paired_category_differences"]}
    for a,b,c in (("mineru","llamaparse","table"),("mineru","docling","math"),("docling","mineru","diagram")):
        r=pair_map.get((a,b,c)); sign=1
        if r is None: r=pair_map[b,a,c]; sign=-1
        lo,hi=(r["ci95_low"],r["ci95_high"]) if sign==1 else (-r["ci95_high"],-r["ci95_low"])
        p(f"{labels[c]}, {names[a]}−{names[b]}: Δ={fmt(sign*r['delta'])} п.п., парный 95% CI [{fmt(lo)}; {fmt(hi)}], n={r['n_documents']} PDF. "+("Интервал включает 0: наблюдённый порядок нельзя считать устойчивым превосходством." if lo<=0<=hi else "Интервал не включает 0 в этой описательной проверке; малая целевая выборка и отсутствие поправки ограничивают переносимость вывода."))

    report.heading("speed","7. Скорость и качество")
    table([{"tool":names[t],"seconds":by[t]["total_processing_time_sec"],"sec_page":fmt(by[t]["sec_per_page"],3),"median":fmt(speeds[t]["median_document_sec_per_page"],3),
            "slow_doc":speeds[t]["slowest_document"],"slow_share":fmt(100*speeds[t]["slowest_document_time_share"])+"%","overall":by[t]["overall_score"]} for t in sorted(by,key=lambda t:by[t]["sec_per_page"])],
          [("tool","Инструмент"),("seconds","Всего, с"),("sec_page","с/стр, корпус"),("median","Median PDF, с/стр"),("slow_doc","Самый медленный PDF"),("slow_share","Доля времени"),("overall","Overall")],"Оригинальные замеры адаптеров; время повторного вычисления метрик исключено","speed_sensitivity")
    p(f"pdfplumber быстрее Docling на этом корпусе примерно в {fmt(by['docling']['sec_per_page']/by['pdfplumber']['sec_per_page'])} раза, но его Math заметно ниже, а Diagram равен нулю. MinerU быстрее Docling примерно в {fmt(by['docling']['sec_per_page']/by['mineru']['sec_per_page'])} раза; у него выше Table, Math и Image, тогда как Docling значительно сильнее по Diagram и лучше по Text. Это разные профили качества, а не взаимозаменяемые решения.")
    for t in ("pdfminer","docling"):
        r=speeds[t]
        p(f"{names[t]}: {r['slowest_document']} занимает {fmt(100*r['slowest_document_time_share'])}% суммарного времени; на нём {fmt(r['max_document_sec_per_page'],3)} с/стр. Общая оценка {fmt(r['corpus_sec_per_page'],3)} с/стр отличается от медианы пяти PDF {fmt(r['median_document_sec_per_page'],3)}. Диагностическое значение без самого медленного документа — {fmt(r['sec_per_page_without_slowest_diagnostic'],3)} с/стр; из основного результата этот документ не исключался.")
    p("Номинальный Pareto frontier по двум осям Overall↑ / sec/page↓: "+", ".join(names[t] for t in ctx["frontier"])+". Каждая следующая точка покупает прирост Overall увеличением наблюдённого времени; неопределённость оценок и профиль категорий frontier не учитывает. Frontier по отдельным категориям сохранён в pareto_frontiers.csv; он включает только инструменты с положительным score категории.")
    p("Local использует adapter wall time, cloud — сохранённый processing_seconds, который у части сервисов совпадает с client latency. В этих измерениях могут различаться upload, ожидание очереди и серверная обработка. Повторных запусков одного задания нет: нельзя строить CI повторяемости latency по пяти разным PDF. Корреляции Spearman в correlations.csv — описания фиксированных 10 участников, без причинной интерпретации и без тестов значимости.")
    report.figure("05_speed"); report.figure("17_speed_by_document"); report.figure("08_quality_speed")

    report.heading("cost","8. Стоимость и качество")
    table([{"tool":names[t],"cost":by[t]["api_cost_usd"],"basis":by[t]["api_cost_basis"],"overall":by[t]["overall_score"]} for t in ordered],
          [("tool","Инструмент"),("cost","API USD / 98 стр."),("basis","Основание"),("overall","Overall")],"Записанные API costs; коммерческая цена из них не следует","tool_scores")
    p("25 local пар имеют not_applicable_local=0; 5 OCR.Space пар — actual=0; 20 остальных cloud пар — estimated=0 с историческими free/trial пояснениями. Estimated=0 не является проверкой счёта поставщика. Текущие тарифы и оплата подписок не исследовались и не подставлялись вместо наблюдённых значений.")
    p("Quality/cost при нулевом знаменателе не определён. Spearman с постоянной осью cost также не определён. Поэтому график quality–cost построен с истинными cost=0 и смещением только подписей, без искусственного разброса точек. Он показывает отсутствие данных для экономического ранжирования, а не одинаковую коммерческую выгодность всех решений.")
    p("Для последующего отдельного стоимостного сценария потребовались бы дата тарифов, фиксированный режим обработки, платный объём, валюта, учёт подписок и повторов, а для local — стоимость оборудования/времени/электроэнергии. Ничего из этого нельзя достоверно получить из текущих api_cost_usd=0. Исторические credits и pricing notes сохранены в operational_provenance.csv без объявления их актуальными ценами.")
    report.figure("07_cost"); report.figure("09_quality_cost")

    report.heading("resources","9. Ресурсы и эксплуатационные ограничения")
    table([{"tool":names[t],"ram":by[t]["peak_ram_mb"],"vram":by[t]["peak_vram_mb"],"scope":"локальный процесс + дочерние" if by[t]["deployment"]=="local" else "только облачный клиент",
            "gpu_basis":"device upper bound + Torch activity" if t in ("docling","mineru") else "0 attributable; не server GPU" if by[t]["deployment"]=="cloud" else "0 attributable"} for t in ordered],
          [("tool","Инструмент"),("ram","RAM, МиБ"),("vram","VRAM, МиБ"),("scope","Область измерения RAM"),("gpu_basis","VRAM basis")],"Пик по пяти исходным запускам; поля _mb содержат единицы 1024² байт","pair_operational")
    p(f"Docling: RAM {fmt(by['docling']['peak_ram_mb']/1024)} ГиБ, VRAM {fmt(by['docling']['peak_vram_mb']/1024)} ГиБ; MinerU: RAM {fmt(by['mineru']['peak_ram_mb']/1024)} ГиБ, VRAM {fmt(by['mineru']['peak_vram_mb']/1024)} ГиБ. Для этих десяти пар использован device-wide GPU upper bound при подтверждённой активности Torch. Это не эксклюзивная память процесса и не минимальные аппаратные требования продукта.")
    p("У CPU tools/cloud-client published VRAM=0 означает отсутствие атрибутированного GPU-использования. Для cloud ресурсы серверов неизвестны. RAM — RSS процесса и его дочерних процессов, включая интерпретатор и импорты; сравнивать её с минимально необходимой памятью библиотеки нельзя. Низкий клиентский RAM облачного API не доказывает меньшую вычислительную стоимость на стороне поставщика.")
    p("Историческая среда исследования: Windows 11, Python 3.12.10, NVIDIA RTX 4070 Laptop 8 GB по CODEX_CONTEXT.md. Анализ не является повторным измерением на Linux или другом сервере. Mindee разбивал длинные PDF на запросы до 10 страниц и собирал исходный документ обратно; чанки не увеличивают число документов или статистических повторов.")
    report.figure("06_resources")

    report.heading("profiles","10. Практические профили всех десяти участников")
    profiles={
        "pymupdf":"Быстрый classic parser и лидер по Text, с ненулевыми Table, Math, Chemistry и Image. По Image практически совпадает с Adobe Extract и pdfminer.six, но работает значительно быстрее pdfminer. Diagram равен нулю.",
        "pdfplumber":"Самая быстрая обработка корпуса. Text остаётся сильным; Table, Math и Chemistry ненулевые, но таблицы сильно зависят от документа. Image ниже лидеров, Diagram равен нулю.",
        "docling":"Лидер по Diagram и по наблюдаемому Overall, с высоким Text/Table и ненулевыми Math, Chemistry и Image. Этот профиль получен ценой максимального времени и RAM/VRAM.",
        "pdfminer":"Профиль текста и изображений, наибольший Chemistry Score и ненулевой Math; Table и Diagram равны нулю. D05 определяет почти всё суммарное время. Image практически совпадает с PyMuPDF и Adobe Extract.",
        "mineru":"Локальная ML-система со вторым Overall и наибольшими Table и Image; Math второй после LlamaParse, Diagram заметно ниже Docling. Она примерно в 27 раз быстрее Docling на этом корпусе, однако остаётся ресурсоёмкой и требует около 5,7 ГиБ RAM и 4,7 ГиБ измеренного VRAM upper bound.",
        "ocr_space":"Сильный результат по Math и полный Chemistry detection, но Text заметно слабее остальных участников и отмечен IQR-предупреждением. Table ненулевой, Image и Diagram равны нулю; время на страницу — второе по величине после Docling.",
        "nutrient":"Сильный облачный профиль Text/Table/Math/Chemistry/Image и третий Overall, без Diagram. Исправление coordinate canvas существенно изменило результат; междокументный разброс таблиц остаётся большим.",
        "mindee":"Высокий Text дополнен ненулевыми Math и Chemistry; Table, Image и Diagram равны нулю. Общий показатель ограничен отсутствием этих трёх типов выхода. Длинные документы проходили chunking; серверные ресурсы неизвестны.",
        "adobe_extract":"Облачный профиль Text/Table/Chemistry/Image и наименьшее наблюдённое sec/page среди cloud. Table существенно зависит от PDF. Math и Diagram равны нулю; клиентские RAM/VRAM не описывают серверную обработку.",
        "llamaparse":"Лидер по Math, Table практически совпадает с лидером, Text высокий и Chemistry ненулевой. Image и Diagram равны нулю; ресурсы серверной стороны неизвестны.",
    }
    for t in by:
        p(names[t]+": "+profiles[t]+f" Сопоставлено {by[t]['matched_objects']}/40 GT; Overall={fmt(by[t]['overall_score'])}.")
    p("Для задачи преимущественно текста на этом корпусе ориентир по качеству — PyMuPDF, по скорости — pdfplumber; для таблиц нужно сопоставлять MinerU, LlamaParse и Nutrient; для математических формул — LlamaParse, MinerU и OCR.Space с проверкой отдельных объектов и типа payload; для диаграмм — Docling; для изображений — MinerU. Это направления выбора для данных benchmark, а не рекомендации безусловно оплачивать или внедрять продукт.")

    report.heading("limitations","11. Ограничения и сохранённые предупреждения")
    p("(1) Всего пять целенаправленно выбранных документов и 40 GT; coverage категорий неравномерен. (2) В GT нет отдельной reading_order разметки для 9 текстовых объектов: действующие веса Text — 0,5 CER quality, 0,375 WER quality, 0,125 edit similarity. Ошибки порядка влияют косвенно, но отдельная оценка reading order не получена. (3) TEDS-like — собственный аналог, не официальный PubTabNet TEDS. (4) Для формул matching использует нормализованное представление, а evaluator раздельно хранит raw и normalized LaTeX; метрика не доказывает математическую эквивалентность.")
    p("(5) GT для image/diagram выборочный, поэтому действует sampled_gt_recall_v1: пропущенный обязательный GT даёт 0, а лишние кандидаты сохраняются только как unmatched_candidate_count без неподтверждённого FP penalty. Page-level precision по этой разметке не оценивается. (6) Отсутствие специализированного объекта и низкое качество распознавания — разные причины нулей; они разделяются через coverage и standardized_object_counts.csv. (7) Cloud/local timing и resource scopes различаются. (8) Стоимость и межзапусковая вариация недостаточны для экономического ранжирования или SLA.")
    table(data["quality_issues"],[("tool","Инструмент"),("metric","Показатель"),("value","Значение"),("code","Предупреждение")],f"Все {warning_count} исходных IQR-предупреждений сохранены","quality_issues")
    warning_counts = {
        tool: sum(row["tool"] == tool for row in data["quality_issues"])
        for tool in by
    }
    warning_distribution = ", ".join(
        f"{names[tool]} — {count}"
        for tool, count in warning_counts.items()
        if count
    )
    p("IQR warnings — наблюдения, не доказательства повреждения данных. Для Chemistry, Diagram и VRAM большая часть инструментов имеет нули, поэтому IQR=0 отмечает любое ненулевое значение. Распределение предупреждений: " + warning_distribution + ". Внутридокументная аномалия времени pdfminer/D05 исследована отдельно и тоже сохранена.")

    report.heading("reproduce","12. Артефакты и воспроизводимость")
    table(data["versions"],[("tool","Инструмент"),("installed_version","Версия пакета"),("deployment","Deployment"),("technology","Группа")],"Версии из сохранённых adapter snapshots; OCR.Space — версия requests, не версия серверного OCR","versions")
    p("Полные model_versions/tier/version находятся в versions.csv. Основная информация о методах взята из исходников evaluation/aggregation.py, evaluation/*_metrics.py, benchmark/matching.py, benchmark/experiment.py и зафиксированных metadata; актуальные рыночные цены не подмешивались в результаты.")
    table([{"table":name+".csv","rows":len(rows)} for name,rows in data.items()],[("table","Таблица"),("rows","Строк")],"Полный набор машинно-читаемых таблиц")
    p("Воспроизведение из корня проекта: .venv-analysis\\Scripts\\python.exe scripts\\analyze_benchmark.py --experiment-id "+ctx["experiment_id"]+". Среда графиков отдельная; её зависимости фиксируются в requirements-analysis-pinned.txt. Основные среды адаптеров не изменялись.")
    p("report.html содержит встроенные SVG и открывается без интернета; report.md содержит ссылки на графики; figures.pdf — 17-страничный атлас; figures/ содержит PNG и SVG; tables/ — CSV; analysis_manifest.json — версии, seed, проверки и хэши входов. Архив рядом с папкой объединяет все артефакты. Полный error analysis и пять примеров на инструмент относятся к следующему промту 14 и здесь не подменяются сравнительными графиками.")
    report.write()
