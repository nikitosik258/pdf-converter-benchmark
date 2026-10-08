"""Standalone PNG/SVG/PDF figures for Prompt 13 (Matplotlib, no network)."""
from __future__ import annotations

import os
from pathlib import Path


def make_figures(ctx):
    out = ctx["out"] / "figures"
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(ctx["out"].parents[2] / ".tools/matplotlib-config"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.colors import LogNorm

    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10,"axes.spines.top":False,
        "axes.spines.right":False,"axes.titleweight":"bold","axes.grid":False,"figure.facecolor":"white",
        "savefig.facecolor":"white","svg.fonttype":"none","pdf.fonttype":42})
    names,labels,cats = ctx["tool_names"],ctx["category_labels"],ctx["categories"]
    tools=ctx["tools"]; by=ctx["by_tool"]; tables=ctx["tables"]
    order=sorted(by,key=lambda t:by[t]["overall_score"],reverse=True)
    color_group={"classic_local":"#167D8D","ml_local":"#BD5A20","cloud_unspecified":"#6557A5"}
    colors={t:color_group[by[t]["technology"]] for t in by}
    ci={(r["tool"],r["category"]):r for r in tables["category_uncertainty"]}
    figures=[]
    atlas=PdfPages(ctx["out"] / "figures.pdf")

    def save(fig,name,title,note):
        fig.suptitle(title,fontsize=16,fontweight="bold",y=1.02)
        fig.text(.015,-.045,note,fontsize=9,color="#465363",va="top")
        for extension in ("png","svg"):
            fig.savefig(out/(name+"."+extension),dpi=180,bbox_inches="tight")
        atlas.savefig(fig,bbox_inches="tight")
        figures.append({"name":name,"title":title,"note":note,"png":"figures/"+name+".png","svg":"figures/"+name+".svg"})
        plt.close(fig)
        print("Figure: "+name,flush=True)

    def heatmap(ax,matrix,row_names,column_names,*,diverging=False,log=False):
        data=np.array(matrix,dtype=float)
        cmap=plt.get_cmap("RdBu_r" if diverging else "YlGnBu").copy(); cmap.set_bad("#e9edf1")
        kwargs={"norm":LogNorm(vmin=float(np.nanmin(data)),vmax=float(np.nanmax(data)))} if log else (
            {"vmin":-max(1,float(np.nanmax(abs(data)))),"vmax":max(1,float(np.nanmax(abs(data))))} if diverging else {"vmin":0,"vmax":100})
        im=ax.imshow(np.ma.masked_invalid(data),cmap=cmap,aspect="auto",**kwargs)
        ax.set_xticks(range(len(column_names)),column_names); ax.set_yticks(range(len(row_names)),row_names)
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                value=data[i,j]
                text="—" if np.isnan(value) else f"{value:.2f}" if log else f"{value:+.1f}" if diverging else f"{value:.1f}"
                normalized=im.norm(value) if not np.isnan(value) else 0
                dark=(normalized<.2 or normalized>.8) if diverging else normalized>.53
                ax.text(j,i,text,ha="center",va="center",fontsize=9,color="white" if dark and not np.isnan(value) else "#172D41")
        return im

    fig,ax=plt.subplots(figsize=(11,6),layout="constrained")
    values=[by[t]["overall_score"] for t in order]
    ax.barh([names[t] for t in order],values,color=[colors[t] for t in order]); ax.invert_yaxis(); ax.set_xlim(0,100)
    for i,v in enumerate(values): ax.text(v+.8,i,f"{v:.2f}",va="center")
    chemistry_is_zero = all(by[t]["chemistry_score"] == 0 for t in by)
    if chemistry_is_zero:
        ax.axvline(100*5/6,color="#9aa4ac",ls="--",lw=1)
    ax.set_xlabel("Overall, 0–100; равные веса шести категорий")
    save(fig,"01_overall","Итоговое качество на фиксированном корпусе",
         (
             "Overall показывает выбранную смесь категорий. Пунктир: 83,33 — потолок при Chemistry=0. Общий CI не оценивался."
             if chemistry_is_zero
             else "Overall показывает выбранную смесь категорий; Chemistry уже имеет ненулевой специализированный выход. Общий CI не оценивался."
         ))

    fig,axs=plt.subplots(2,3,figsize=(18,11),layout="constrained")
    for ax,c in zip(axs.flat,cats):
        ordered=sorted(by,key=lambda t:by[t][c+"_score"],reverse=True)
        for i,t in enumerate(ordered):
            rec=ci[t,c]; value=rec["score"]
            ax.errorbar(value,i,xerr=[[max(0,value-rec["ci95_low"])],[max(0,rec["ci95_high"]-value)]],fmt="o",color=colors[t],capsize=3)
        ax.set_yticks(range(10),[names[t] for t in ordered]); ax.invert_yaxis(); ax.set_xlim(-2,102)
        ax.set_title(f"{labels[c]} · {ci[ordered[0],c]['n_documents']} PDF / {ci[ordered[0],c]['n_objects']} GT")
        ax.set_xlabel("Category Score / описательный 95% CI"); ax.grid(axis="x",alpha=.2)
    save(fig,"02_categories_ci","Качество по шести категориям и неопределённость",
         "Сохранённые percentile bootstrap CI: 10 000 выборок, seed=42. Химия: один PDF, только within-document CI; не междокументный вывод.")

    fig,axs=plt.subplots(2,5,subplot_kw={"projection":"polar"},figsize=(21,10))
    fig.subplots_adjust(left=.04,right=.96,bottom=.08,top=.88,wspace=.35,hspace=.65)
    angles=np.linspace(0,2*np.pi,6,endpoint=False); closed=np.r_[angles,angles[0]]
    for ax,t in zip(axs.flat,order):
        vals=[by[t][c+"_score"] for c in cats]; vals.append(vals[0])
        ax.set_theta_offset(np.pi/2); ax.set_theta_direction(-1); ax.plot(closed,vals,color=colors[t],lw=2); ax.fill(closed,vals,color=colors[t],alpha=.15)
        ax.set_xticks(angles,["Текст","Таблицы","Мат.","Химия","Изобр.","Диагр."],fontsize=8); ax.set_ylim(0,100)
        ax.set_yticks([25,50,75,100],["25","50","75","100"],fontsize=7); ax.set_title(names[t],pad=23,fontsize=12); ax.grid(alpha=.35)
    save(fig,"03_radar","Профили инструментов: одинаковые оси и шкала",
         "Отдельная панель для каждого инструмента. Площадь многоугольника не является дополнительной метрикой качества.")

    fig,ax=plt.subplots(figsize=(11,6.5),layout="constrained")
    im=heatmap(ax,[[by[t][c+"_score"] for c in cats] for t in order],[names[t] for t in order],[labels[c] for c in cats])
    fig.colorbar(im,ax=ax,label="Category Score, 0–100")
    save(fig,"04_heatmap","Инструмент × категория",
         "Ноль при существующем GT — отсутствие оцениваемого предсказания либо нулевой результат. Химия остаётся обязательной категорией.")

    fig,ax=plt.subplots(figsize=(11,6),layout="constrained")
    speed_order=sorted(by,key=lambda t:by[t]["sec_per_page"])
    for i,t in enumerate(speed_order):
        vals=[p["sec_per_page"] for p in tables["pair_operational"] if p["tool"]==t]
        ax.plot([min(vals),max(vals)],[i,i],color=colors[t],alpha=.35,lw=4)
        ax.scatter(by[t]["sec_per_page"],i,c=colors[t],s=65,zorder=3)
        ax.annotate(f"{by[t]['sec_per_page']:.3f}",(by[t]["sec_per_page"],i),xytext=(7,5),textcoords="offset points",fontsize=9)
    ax.set_yticks(range(10),[names[t] for t in speed_order]); ax.invert_yaxis(); ax.set_xscale("log"); ax.set_xlim(.025,400)
    ax.set_xlabel("Секунд на страницу, логарифмическая ось; меньше — быстрее"); ax.grid(axis="x",alpha=.2)
    save(fig,"05_speed","Скорость: общий корпус и разброс между PDF",
         "Точка = сумма времени / 98 страниц. Отрезок = min–max по пяти PDF, не доверительный интервал. Cloud/local timing scopes различаются.")

    fig,axs=plt.subplots(1,2,figsize=(15,6),layout="constrained")
    for ax,field,title in zip(axs,("peak_ram_mb","peak_vram_mb"),("RAM: процесс / облачный клиент","VRAM: атрибутированная память")):
        vals=[by[t][field]/1024 for t in order]
        bars=ax.barh([names[t] for t in order],vals,color=[colors[t] for t in order]); ax.invert_yaxis()
        for i,(bar,t,v) in enumerate(zip(bars,order,vals)):
            if by[t]["deployment"]=="cloud": bar.set_hatch("///")
            ax.text(v+.05,i,f"{v:.3f}",va="center",fontsize=9)
        ax.set_title(title); ax.set_xlabel("ГиБ (1024 МиБ)"); ax.set_xlim(0,max(vals)*1.18)
    save(fig,"06_resources","Пиковые RAM и VRAM",
         "Штриховка: только облачный клиент, ресурсы поставщика неизвестны. Docling/MinerU VRAM — device-wide upper bound, не process-exclusive.")

    fig,ax=plt.subplots(figsize=(11,6),layout="constrained")
    for i,t in enumerate(order):
        ax.scatter(0,i,color=colors[t],s=65)
        ax.text(.003,i,f"$0 · {by[t]['api_cost_basis']}",va="center")
    ax.set_yticks(range(10),[names[t] for t in order]); ax.invert_yaxis(); ax.set_xlim(-.001,.025); ax.set_xticks([0],["$0"])
    ax.set_xlabel("Записанные API-затраты на 98 страниц")
    save(fig,"07_cost","Наблюдённые затраты: все записи равны нулю",
         "Local: not applicable; OCR.Space: actual; другие cloud: estimated/free/trial. Это не сравнение коммерческих тарифов и не полная стоимость владения.")

    fig,ax=plt.subplots(figsize=(12,7),layout="constrained")
    offsets={"pymupdf":(8,10),"pdfplumber":(8,-17),"docling":(-63,12),"pdfminer":(8,-18),"mineru":(-70,-12),"ocr_space":(8,8),"nutrient":(8,-12),"mindee":(8,-12),"adobe_extract":(-77,-16),"llamaparse":(8,10)}
    for t in by:
        r=by[t]; marker="s" if r["technology"]=="ml_local" else "^" if r["deployment"]=="cloud" else "o"
        ax.scatter(r["sec_per_page"],r["overall_score"],color=colors[t],marker=marker,s=90)
        ax.annotate(names[t],(r["sec_per_page"],r["overall_score"]),xytext=offsets[t],textcoords="offset points",fontsize=10)
    front=ctx["frontier"]
    ax.plot([by[t]["sec_per_page"] for t in front],[by[t]["overall_score"] for t in front],ls="--",color="#6e7b87",alpha=.7,label="Номинальный Pareto frontier")
    quality_top=max(r["overall_score"] for r in by.values())
    quality_limit=max(55,5*(int(quality_top/5)+2))
    ax.set_xscale("log"); ax.set_xlim(.05,130); ax.set_ylim(0,quality_limit); ax.set_xlabel("Секунд/страницу, log; меньше — быстрее"); ax.set_ylabel(f"Overall Score, 0–100 (показан диапазон 0–{quality_limit})")
    ax.legend(loc="upper left"); ax.grid(alpha=.2)
    save(fig,"08_quality_speed","Качество и скорость",
         "Frontier учитывает только Overall и наблюдённое sec/page. Он не учитывает неопределённость, специализацию, ресурсы и разный timing scope.")

    fig,ax=plt.subplots(figsize=(11,7),layout="constrained")
    quality_order=sorted(by,key=lambda t:by[t]["overall_score"])
    for t,label_y in zip(quality_order,np.linspace(2,quality_limit-2,10)):
        value=by[t]["overall_score"]; ax.scatter(0,value,color=colors[t],s=65)
        ax.annotate(f"{names[t]} · {value:.2f}",(0,value),xytext=(.013,float(label_y)),textcoords="data",
                    arrowprops={"arrowstyle":"-","color":colors[t],"lw":.8},fontsize=10,color=colors[t],va="center")
    ax.set_xlim(-.002,.055); ax.set_ylim(0,quality_limit); ax.set_xticks([0],["$0"]); ax.set_xlabel("Наблюдённые API-затраты, USD / 98 страниц")
    ax.set_ylabel(f"Overall Score, 0–100 (показан диапазон 0–{quality_limit})")
    save(fig,"09_quality_cost","Качество и стоимость: вырожденная ось затрат",
         "Все точки действительно имеют cost=0; смещены только подписи. Quality/cost и корреляция с cost не определены. Ценовое ранжирование невозможно.")

    fig,ax=plt.subplots(figsize=(11,6),layout="constrained")
    im=heatmap(ax,[[ctx["doc_scores"][t,d,"text"] for d in ctx["documents"]] for t in order],[names[t] for t in order],ctx["documents"])
    fig.colorbar(im,ax=ax,label="Text document score")
    save(fig,"10_document_text","Зависимость качества текста от документа",
         "Все пять PDF содержат текстовую GT-разметку; каждая ячейка — среднее текстовых GT этого документа.")

    fig,axs=plt.subplots(2,3,figsize=(18,11),layout="constrained")
    for ax,c in zip(axs.flat,cats):
        im=heatmap(ax,[[ctx["doc_scores"].get((t,d,c),np.nan) for d in ctx["documents"]] for t in order],[names[t] for t in order],ctx["documents"])
        ax.set_title(labels[c]); ax.tick_params(axis="y",labelsize=8)
    fig.colorbar(im,ax=list(axs.flat),label="Document category score",shrink=.75)
    save(fig,"11_document_categories","Инструмент × PDF отдельно по каждой категории",
         "Серый прочерк = в PDF нет GT данной категории. Ноль = категория размечена, но оценка нулевая. Эти случаи не взаимозаменяемы.")

    fig,ax=plt.subplots(figsize=(10,5),layout="constrained")
    diff=sorted(tables["category_difficulty"],key=lambda r:r["mean_across_fixed_tools"])
    ax.barh([labels[r["category"]] for r in diff],[r["mean_across_fixed_tools"] for r in diff],color="#167D8D")
    for i,r in enumerate(diff):
        ax.scatter(r["median_across_fixed_tools"],i,marker="D",color="#BD5A20",s=45,zorder=3)
        ax.text(r["mean_across_fixed_tools"]+1,i,f"{r['mean_across_fixed_tools']:.2f} · ненулевые: {r['tools_with_nonzero_score']}/10",va="center",fontsize=9)
    ax.set_xlim(0,100); ax.set_xlabel("Среднее десяти Category Scores; ромб — медиана инструментов")
    save(fig,"12_category_difficulty","Какие категории дают наиболее низкие оценки",
         "Описывает этот фиксированный набор адаптеров. Нули Diagram/Math отражают также отсутствие специализированного выхода, а не только OCR-сложность.")

    fig,axs=plt.subplots(1,2,figsize=(15,5),layout="constrained")
    groups={r["group"]:r for r in tables["group_scores"]}
    for ax,comparison in zip(axs,(("local","cloud"),("classic_local","ml_local"))):
        x=np.arange(6)
        for i,g in enumerate(comparison):
            ax.bar(x+(i-.5)*.36,[groups[g][c+"_score"] for c in cats],width=.34,label=ctx["group_labels"][g],color=("#167D8D","#6557A5" if g=="cloud" else "#BD5A20")[i])
        ax.set_xticks(x,[labels[c] for c in cats],rotation=25,ha="right"); ax.set_ylim(0,100); ax.legend(fontsize=9); ax.set_ylabel("Среднее Category Score инструментов")
    save(fig,"13_groups","Группы инструментов: два разных разбиения",
         "Cloud/local — место исполнения. Classic/ML сравниваются только среди local. Группы — фиксированные участники benchmark, не случайная выборка продуктов.")

    fig,axs=plt.subplots(2,3,figsize=(18,11),layout="constrained")
    for ax,c in zip(axs.flat,cats):
        vals=[[r["object_score"] for r in tables["object_scores"] if r["tool"]==t and r["category"]==c] for t in order]
        bp=ax.boxplot(vals,orientation="horizontal",tick_labels=[names[t] for t in order],showmeans=True,patch_artist=True,
                      meanprops={"marker":"D","markerfacecolor":"#BD5A20","markeredgecolor":"white","markersize":4})
        for box,t in zip(bp["boxes"],order): box.set_facecolor(colors[t]); box.set_alpha(.35)
        ax.invert_yaxis(); ax.set_xlim(-2,102); ax.set_title(labels[c]); ax.tick_params(axis="y",labelsize=8)
        ax.set_xlabel("Object Score; ромб — среднее объектов")
    save(fig,"14_object_distributions","Разброс качества между объектами",
         "Boxplot: медиана, Q1–Q3, whiskers 1.5 IQR, все выбросы сохранены. Среднее объектов здесь диагностическое; canonical score использует macro по PDF.")

    fig,ax=plt.subplots(figsize=(11,6),layout="constrained")
    lodo={(r["tool"],r["omitted_document"]):r["overall_delta"] for r in tables["leave_one_document_out"]}
    im=heatmap(ax,[[np.nan if lodo[t,d] is None else lodo[t,d] for d in ctx["documents"]] for t in order],[names[t] for t in order],ctx["documents"],diverging=True)
    fig.colorbar(im,ax=ax,label="ΔOverall, процентных пунктов"); ax.set_xlabel("Исключаемый документ — только диагностическая проверка")
    save(fig,"15_document_sensitivity","Чувствительность Overall к составу корпуса",
         "После исключения D02 нет Chemistry GT: Overall не вычисляется, веса не перенормируются. В основном результате все пять PDF сохранены.")

    fig,ax=plt.subplots(figsize=(11,6),layout="constrained")
    pair_map={(r["left"],r["right"],r["category"]):r for r in tables["paired_category_differences"]}
    comparisons=[("pymupdf","nutrient","text"),("mineru","llamaparse","table"),("mineru","docling","math"),
                 ("mineru","nutrient","math"),("docling","mineru","diagram"),("mineru","adobe_extract","image")]
    ticks=[]
    for i,(a,b,c) in enumerate(comparisons):
        r=pair_map.get((a,b,c)); sign=1
        if r is None: r=pair_map[b,a,c]; sign=-1
        v=r["delta"]*sign; lo,hi=(r["ci95_low"],r["ci95_high"]) if sign==1 else (-r["ci95_high"],-r["ci95_low"])
        ax.errorbar(v,i,xerr=[[max(0,v-lo)],[max(0,hi-v)]],fmt="o",capsize=4,color="#167D8D")
        ticks.append(f"{labels[c]}: {names[a]} − {names[b]} (n={r['n_documents']})")
    ax.set_yticks(range(len(ticks)),ticks); ax.invert_yaxis(); ax.axvline(0,color="#8995a0",ls="--"); ax.set_xlim(-100,100)
    ax.set_xlabel("Разность Category Score, п.п.; парный 95% bootstrap CI")
    save(fig,"16_paired_differences","Парные сравнения на одних и тех же PDF",
         "Исследовательские интервалы без поправки на множественные сравнения. Они не устанавливают универсального победителя; n — число PDF, не 400 строк.")

    fig,ax=plt.subplots(figsize=(11,6),layout="constrained")
    pmap={(r["tool"],r["document_id"]):r["sec_per_page"] for r in tables["pair_operational"]}
    im=heatmap(ax,[[pmap[t,d] for d in ctx["documents"]] for t in order],[names[t] for t in order],ctx["documents"],log=True)
    fig.colorbar(im,ax=ax,label="Секунд/страницу, логарифмическая цветовая шкала")
    save(fig,"17_speed_by_document","Скорость на каждом PDF",
         "Единичные сохранённые запуски разных документов. Междокументный разброс не является повторяемостью времени выполнения одного задания.")
    atlas.close()
    return figures
