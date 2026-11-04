"""Generate durable management reports from an immutable, measured report snapshot."""

from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import textwrap
from xml.sax.saxutils import escape

RU = {
    "Store / work order": "Магазин / задача",
    "Status": "Статус",
    "Open": "Открыто",
    "In progress": "В работе",
    "Owner": "Ответственный",
    "Refill shelf": "Пополнить полку",
    "Check shelf stock": "Пересчитать остатки",
    "Store task": "Задача магазина",
    "Due time": "Срок",
    "Unassigned": "Не назначено",
    "open": "Открыто",
    "in_progress": "В работе",
    "Open work at generation time": "Открытые задачи на момент отчёта",
    "Shelf / product": "Полка / товары",
    "Stock / capacity": "Остаток / ёмкость",
    "Refill at": "Порог",
    "Add to fill": "Добавить",
    "Shelf stock at generation time": "Остатки полок на момент отчёта",
    "Popular routes": "Популярные маршруты",
    "Route and camera": "Маршрут и камера",
    "Operator-confirmed stock at report generation, separate from the activity period. Pickup predictions do not change inventory.": "Остатки подтверждены оператором на момент создания отчёта и не относятся к периоду активности. Предсказания подборов не изменяют остатки.",
    "Unavailable": "Нет данных",
    "Selected recording collection": "Выбранные записи",
    "Recording and camera": "Запись и камера",
    "Tracks": "Треки",
    "Pickups": "Подборы",
    "Observed time": "Время наблюдения",
    "Area and camera": "Зона и камера",
    "Visits": "Визиты",
    "Observed dwell": "Время в зоне",
    "Signal and source time": "Сигнал и время в записи",
    "Camera": "Камера",
    "Review": "Проверка",
    "Operator notes": "Заметки оператора",
    "No notes": "Без заметок",
    "Recording date": "Дата записи",
    "Track IDs present": "Треки",
    "Pickup candidates": "Подборы",
    "Recordings": "Записи",
    "Operator summary": "Заметки управляющего",
    "Recording coverage": "Охват записей",
    "Area performance": "Показатели зон",
    "Activity by recording date": "Активность по датам",
    "Signals and operator review": "Сигналы и проверка",
    "Pickups against the till": "Сверка подборов с кассой",
    "Outcome": "Итог",
    "Count": "Количество",
    "Left without reaching a till": "Ушёл мимо кассы",
    "Track lost before a till": "Трек оборвался до кассы",
    "Still in view when the recording ended": "Ещё в кадре в конце записи",
    "Reached a till": "Дошёл до кассы",
    "No checkout on this camera": "Кассы нет в кадре",
    "Coverage and interpretation": "Охват и интерпретация",
    "Reproducibility": "Воспроизводимость",
    "No measured results available for this section.": "Для этого раздела нет измеренных результатов.",
    "pending": "ждёт",
    "accepted": "принято",
    "rejected": "отклонено",
    "Pickup candidate": "Подбор товара",
    "Track counts are summed camera-local IDs, not deduplicated customers.": "Треки это сумма идентификаторов внутри камер, а не уникальные покупатели.",
    "Pickup candidates require review and do not establish purchases, payment or theft.": "Подборы требуют проверки и не подтверждают покупку, оплату или кражу.",
    "One completed run per recording is used; area metrics use the specified saved geometry.": "Для каждой записи берётся один завершённый анализ и указанная разметка.",
    "Simulated data. These numbers are generated, not measured in a shop.": "Симуляция. Эти числа сгенерировал симулятор, в реальном магазине ничего не измерялось.",
    "Collection report: footage is selected explicitly and is not attributed to a calendar shift or month.": "Отчёт по выбранным записям: они не привязаны к смене или месяцу.",
    "Period selection uses operator-assigned recording times; samples outside the selected interval are excluded.": "Период считается по времени, заданному оператором. Данные вне периода исключены.",
    "No analysed recordings match this scope. This is an empty coverage report, not evidence of zero store activity.": "За этот период нет проанализированных записей. Это отсутствие данных, а не отсутствие покупателей.",
    "Bakery": "Хлеб",
    "Dairy": "Молочка",
    "Drinks": "Напитки",
    "Fruit & veg": "Овощи и фрукты",
    "Household": "Бытовая химия",
    "Snacks": "Снеки",
    "Grocery": "Бакалея",
    "Sweets": "Сладости",
    "Tea & coffee": "Чай и кофе",
    "Promo stand": "Акции",
    "Queue": "Очередь",
    "Checkout": "Касса",
    "Overhead camera 01": "Камера 01 над залом",
}


def translator(report):
    return (lambda text: RU.get(text, text)) if report.get("lang") == "ru" else (lambda text: text)


def label(value, tr=lambda text: text):
    return str(value if value is not None else tr("Unavailable"))


def duration(value, tr=lambda text: text):
    if value is None:
        return tr("Unavailable")
    russian = tr("Unavailable") != "Unavailable"
    if value >= 3600:
        text = f"{value / 3600:.2f} ч" if russian else f"{value / 3600:.2f} h"
    elif value >= 60:
        text = f"{value / 60:.1f} мин" if russian else f"{value / 60:.1f} min"
    else:
        text = f"{value:.1f} с" if russian else f"{value:.1f} s"
    return text.replace(".", ",") if russian else text


def period(report):
    p = report["period"]
    tr = translator(report)
    if report["scope"] == "collection":
        return tr("Selected recording collection")
    joiner = "по" if report.get("lang") == "ru" else "to"
    return f"{p['start']} {joiner} {p['end']} ({p['timezone']})"


def opening(report):
    s = report["summary"]
    n = len(report["recordings"])
    tr = translator(report)
    russian = report.get("lang") == "ru"
    if not n:
        return (
            "За выбранный период нет завершённых анализов. Оценить активность магазина по этому отчёту нельзя."
            if russian
            else "No completed analyses match the selected period and recordings. Store activity cannot be assessed from this report because analysed coverage is unavailable."
        )
    if russian:
        return f"Отчёт охватывает проанализированных записей: {n}. Треков внутри камер: {s['tracks']}, подборов: {label(s['pickups'], tr)}, сигналов ждёт проверки: {s['pending']}. Треки не являются числом уникальных покупателей."
    return f"This report covers {n} analysed recordings with {s['tracks']} camera-local observed track IDs and {label(s['pickups'])} pickup candidates. There are {s['pending']} pending signals for operator review. Track IDs are not a deduplicated customer count."


def traffic_line(report):
    s = report["summary"]
    tr = translator(report)
    if report.get("lang") == "ru":
        line = f"Просмотрено: {duration(s['observed_seconds'], tr)}. Максимум треков в одном кадре: {s['peak_people']}. Входов в зоны: {s['area_visits']}. Принято сигналов: {s['accepted']}, отклонено: {s['rejected']}."
        if s.get("entries") is not None:
            line += f" Вошло: {s['entries']}, вышло: {s['exits']}."
        return line
    line = f"Observed footage: {duration(s['observed_seconds'])}. Peak simultaneous observed tracks in one view: {s['peak_people']}. Area entries: {s['area_visits']}. Accepted signals: {s['accepted']}; dismissed: {s['rejected']}."
    if s.get("entries") is not None:
        line += f" Entries: {s['entries']}. Exits: {s['exits']}."
    return line


def check_rows(report):
    tr = translator(report)
    checks = report.get("crosscheck") or {}
    rows = [[tr("Outcome"), tr("Count")]]
    for key, text in (
        ("left_without_checkout", "Left without reaching a till"),
        ("lost_before_checkout", "Track lost before a till"),
        ("still_in_view", "Still in view when the recording ended"),
        ("reached_checkout", "Reached a till"),
        ("checkout_not_mapped", "No checkout on this camera"),
    ):
        if checks.get(key):
            rows.append([tr(text), checks[key]])
    return rows


def sections(report):
    tr = translator(report)
    recordings = [
        [tr("Recording and camera"), tr("Tracks"), tr("Pickups"), tr("Observed time")]
    ]
    for r in report["recordings"]:
        recordings.append(
            [
                tr(r["name"]) + "\n" + r["camera_id"],
                r["summary"]["tracks"],
                r["summary"]["pickups"],
                duration(r["summary"]["observed_seconds"], tr),
            ]
        )
    areas = [
        [
            tr("Area and camera"),
            tr("Tracks"),
            tr("Visits"),
            tr("Observed dwell"),
            tr("Pickups"),
        ]
    ]
    for a in sorted(report["areas"], key=lambda a: -a["unique_tracks"]):
        areas.append(
            [
                tr(a["name"])
                + (
                    " / " + a["product_type"]
                    if a.get("product_type") and a["product_type"] != a["name"]
                    else ""
                )
                + "\n"
                + a["camera_id"],
                a["unique_tracks"],
                a["visits"],
                duration(a["observed_dwell_seconds"], tr),
                a["pickup_alerts"],
            ]
        )
    incidents = [
        [tr("Signal and source time"), tr("Camera"), tr("Review"), tr("Operator notes")]
    ]
    for e in report["incidents"][:50]:
        incidents.append(
            [
                tr(e["name"]) + f"\n{e['media_ms'] / 1000:.1f} s",
                e["camera_id"],
                tr(e["review"]["status"]),
                e["review"].get("notes", "") or tr("No notes"),
            ]
        )
    dates = [
        [
            tr("Recording date"),
            tr("Track IDs present"),
            tr("Pickup candidates"),
            tr("Recordings"),
        ]
    ] + [
        [d["date"], d["tracks"], d["pickups"], d["recordings"]] for d in report["daily"]
    ]
    return recordings, areas, incidents, dates


def build_documents(report, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    build_pdf(report, folder / "report.pdf")
    build_docx(report, folder / "report.docx")


def build_pdf(report, path):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        LongTable,
        TableStyle,
    )

    regular = Path(__file__).parent / "assets/fonts/DejaVuSans.ttf"
    bold = Path(__file__).parent / "assets/fonts/DejaVuSans-Bold.ttf"
    font = "Helvetica"
    strong = "Helvetica-Bold"
    if regular.is_file() and bold.is_file():
        if "SentientSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("SentientSans", str(regular)))
            pdfmetrics.registerFont(TTFont("SentientBold", str(bold)))
        font, strong = "SentientSans", "SentientBold"
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="BrandS",
            fontName=strong,
            fontSize=10,
            textColor=colors.HexColor("#101c33"),
            spaceAfter=14,
        )
    )
    styles.add(
        ParagraphStyle(
            name="TitleS",
            fontName=strong,
            fontSize=25,
            leading=31,
            spaceAfter=12,
            textColor=colors.black,
        )
    )
    styles.add(
        ParagraphStyle(
            name="BodyS", fontName=font, fontSize=9, leading=14, spaceAfter=9
        )
    )
    styles.add(
        ParagraphStyle(
            name="FlagS",
            fontName=strong,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#c42a17"),
            spaceAfter=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="MutedS",
            fontName=font,
            fontSize=8,
            leading=12,
            textColor=colors.HexColor("#5f6c84"),
            spaceAfter=11,
        )
    )
    styles.add(
        ParagraphStyle(
            name="HeadS",
            fontName=strong,
            fontSize=12,
            leading=16,
            spaceBefore=18,
            spaceAfter=9,
            keepWithNext=True,
        )
    )
    styles.add(ParagraphStyle(name="CellS", fontName=font, fontSize=8, leading=11))
    styles.add(
        ParagraphStyle(
            name="HeaderS",
            fontName=strong,
            fontSize=8,
            leading=11,
            textColor=colors.white,
        )
    )

    tr = translator(report)

    def para(text, style="BodyS"):
        return Paragraph(escape(label(text, tr)).replace("\n", "<br/>"), styles[style])

    story = [
        para("SENTIENT", "BrandS"),
        para(report["title"], "TitleS"),
        para(period(report), "MutedS"),
    ]
    if report.get("simulated"):
        story.append(para(tr(report["warnings"][0]), "FlagS"))
    story += [
        para(opening(report)),
        para(traffic_line(report)),
    ]
    if len(report["daily"]) == 1:
        story.append(para(tr("Activity by recording date") + ": " + report["daily"][0]["date"], "MutedS"))
    if report.get("notes"):
        story.extend([para(tr("Operator summary"), "HeadS"), para(report["notes"])])

    def table(title, rows, widths):
        story.append(para(title, "HeadS"))
        if len(rows) < 2:
            story.append(
                para(tr("No measured results available for this section."), "MutedS")
            )
            return
        data = [
            [para(v, "HeaderS" if i == 0 else "CellS") for v in row]
            for i, row in enumerate(rows)
        ]
        t = LongTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#101c33")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f4f6f9")],
                    ),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#e6eaf0")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(t)

    recordings, areas, incidents, dates = sections(report)
    width = 468
    table(tr("Recording coverage"), recordings, [240, 60, 60, 108])
    if len(check_rows(report)) > 1:
        table(tr("Pickups against the till"), check_rows(report), [348, 120])
    table(tr("Area performance"), areas, [215, 55, 45, 88, 65])
    stock_rows = [
        [
            tr("Shelf / product"),
            tr("Stock / capacity"),
            tr("Refill at"),
            tr("Add to fill"),
        ]
    ]
    seen = set()
    for snapshot in report.get("shelf_operations", []):
        for shelf in snapshot["shelves"]:
            key = (snapshot["store_id"], snapshot["camera_id"], shelf["id"])
            if key in seen:
                continue
            seen.add(key)
            stock = shelf["stock"]
            stock_rows.append(
                [
                    tr(shelf["name"])
                    + (
                        " / " + shelf["product_type"]
                        if shelf["product_type"]
                        and shelf["product_type"] != shelf["name"]
                        else ""
                    )
                    + "\n"
                    + snapshot["camera_id"],
                    f"{stock['on_hand']} / {stock['capacity']}" if stock else None,
                    stock["reorder_at"] if stock else None,
                    shelf["refill_units"],
                ]
            )
    if len(stock_rows) > 1:
        table(tr("Shelf stock at generation time"), stock_rows, [234, 90, 72, 72])
        story.append(
            para(
                tr(
                    "Operator-confirmed stock at report generation, separate from the activity period. Pickup predictions do not change inventory."
                ),
                "MutedS",
            )
        )
    route_rows = [[tr("Route and camera"), tr("Tracks")]]
    for recording in report["recordings"]:
        for route in recording.get("routes", {}).get("routes", [])[:8]:
            route_rows.append(
                [
                    " > ".join(tr(step["name"]) for step in route["steps"])
                    + "\n"
                    + recording["camera_id"],
                    route["count"],
                ]
            )
    if len(route_rows) > 1:
        table(tr("Popular routes"), route_rows, [388, 80])
    tasks = [[tr("Store / work order"), tr("Status"), tr("Owner"), tr("Due time")]]
    for snapshot in report.get("work_orders", []):
        for task in snapshot["tasks"]:
            if task["status"] in ("open", "in_progress"):
                tasks.append([snapshot["name"] + "\n" + tr({"refill":"Refill shelf", "audit":"Check shelf stock", "manual":"Store task"}[task["kind"]]) + ": " + task["title"], tr({"open":"Open","in_progress":"In progress"}[task["status"]]), task["assignee"] or tr("Unassigned"), datetime.fromisoformat(task["due_at"]).astimezone(ZoneInfo(report["period"]["timezone"])).strftime("%Y-%m-%d\n%H:%M") if task.get("due_at") else tr("Unavailable")])
    if len(tasks) > 1:
        table(tr("Open work at generation time"), tasks, [210, 65, 85, 108])
    if len(report["daily"]) != 1:
        table(tr("Activity by recording date"), dates, [150, 110, 110, 98])
    table(tr("Signals and operator review"), incidents, [160, 95, 65, 148])
    if len(report["incidents"]) > 50:
        story.append(
            para(
                f"Показаны первые 50 из {len(report['incidents'])} сигналов. Все сигналы есть в JSON."
                if report.get("lang") == "ru"
                else f"The first 50 of {len(report['incidents'])} signals are shown. The JSON report contains every signal.",
                "MutedS",
            )
        )
    story.append(para(tr("Coverage and interpretation"), "HeadS"))
    for warning in report["warnings"]:
        story.append(para(tr(warning)))
    if report["excluded"]:
        from collections import Counter

        counts = Counter(e["reason"] for e in report["excluded"])
        story.append(
            para(
                "Excluded recordings: "
                + ", ".join(
                    f"{n} with {reason.lower()}" for reason, n in counts.items()
                )
                + "."
            )
        )
    story.append(para(tr("Reproducibility"), "HeadS"))
    story.append(
        para(
            f"Report ID: {report.get('id', 'preview')}. Generated: {report['created_at']}. The companion JSON contains run IDs, model IDs, saved geometry revisions and the full review snapshot. Area CSV is provided separately.",
            "MutedS",
        )
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#e6eaf0"))
        canvas.line(72, 47, 540, 47)
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor("#5f6c84"))
        canvas.drawString(72, 34, "SENTIENT")
        canvas.drawRightString(540, 34, f"{doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(
        str(path),
        pagesize=(612, 792),
        rightMargin=72,
        leftMargin=72,
        topMargin=54,
        bottomMargin=62,
        title=report["title"],
        author="Sentient Operations",
    ).build(story, onFirstPage=footer, onLaterPages=footer)


def build_docx(report, path):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

    doc = Document()
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = section.right_margin = Inches(1)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(8)
    title = doc.styles["Title"]
    title.font.name = "Calibri"
    title.font.size = Pt(26)
    title.font.color.rgb = RGBColor(0, 0, 0)
    # The bundled Word template can carry a blue Title border in its styles.
    for style in doc.styles:
        for border in list(style.element.iter(qn("w:pBdr"))):
            border.getparent().remove(border)
    for name in ("Heading 1", "Heading 2"):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.color.rgb = RGBColor.from_string("101C33")
        style.paragraph_format.space_before = Pt(16)
        style.paragraph_format.space_after = Pt(8)
    tr = translator(report)
    doc.add_paragraph("SENTIENT").runs[0].font.size = Pt(9)
    doc.add_paragraph(report["title"], style="Title")
    doc.add_paragraph(period(report))
    if report.get("simulated"):
        flag = doc.add_paragraph().add_run(tr(report["warnings"][0]))
        flag.bold = True
        flag.font.color.rgb = RGBColor.from_string("C42A17")
    doc.add_paragraph(opening(report))
    doc.add_paragraph(traffic_line(report))
    if report.get("notes"):
        doc.add_heading(tr("Operator summary"), 1)
        doc.add_paragraph(report["notes"])

    def table(heading, rows, widths):
        doc.add_heading(heading, 1)
        if len(rows) < 2:
            doc.add_paragraph(tr("No measured results available for this section."))
            return
        t = doc.add_table(rows=1, cols=len(rows[0]))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        for col, width in zip(t.columns, widths):
            col.width = Inches(width)
        for i, row in enumerate(rows):
            cells = t.rows[0].cells if i == 0 else t.add_row().cells
            for j, (cell, value) in enumerate(zip(cells, row)):
                cell.width = Inches(widths[j])
                cell.text = label(value, tr)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                props = cell._tc.get_or_add_tcPr()
                shading = OxmlElement("w:shd")
                shading.set(
                    qn("w:fill"),
                    "101C33" if i == 0 else "F4F6F9" if i % 2 == 0 else "FFFFFF",
                )
                props.append(shading)
                borders = OxmlElement("w:tcBorders")
                for edge in ("top", "left", "bottom", "right"):
                    el = OxmlElement("w:" + edge)
                    el.set(qn("w:val"), "single")
                    el.set(qn("w:sz"), "4")
                    el.set(qn("w:color"), "E6EAF0")
                    borders.append(el)
                props.append(borders)
                margins = OxmlElement("w:tcMar")
                for edge in ("top", "left", "bottom", "right"):
                    el = OxmlElement("w:" + edge)
                    el.set(qn("w:w"), "90")
                    el.set(qn("w:type"), "dxa")
                    margins.append(el)
                props.append(margins)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(2)
                    p.paragraph_format.space_before = Pt(2)
                    for run in p.runs:
                        run.font.size = Pt(9)
                        run.bold = i == 0
                        run.font.color.rgb = (
                            RGBColor(255, 255, 255) if i == 0 else RGBColor(16, 28, 51)
                        )
            if i == 0:
                repeat = OxmlElement("w:tblHeader")
                t.rows[0]._tr.get_or_add_trPr().append(repeat)
        doc.add_paragraph().paragraph_format.space_after = Pt(1)

    recordings, areas, incidents, dates = sections(report)
    table(tr("Recording coverage"), recordings, [3.25, 0.8, 0.8, 1.65])
    if len(check_rows(report)) > 1:
        table(tr("Pickups against the till"), check_rows(report), [4.8, 1.7])
    table(tr("Area performance"), areas, [3.1, 0.6, 0.6, 1.25, 0.95])
    table(tr("Activity by recording date"), dates, [2, 1.5, 1.5, 1.5])
    table(tr("Signals and operator review"), incidents, [2.2, 1.3, 0.9, 2.1])
    if len(report["incidents"]) > 50:
        doc.add_paragraph(
            f"Показаны первые 50 из {len(report['incidents'])} сигналов. Все сигналы есть в JSON."
            if report.get("lang") == "ru"
            else f"The first 50 of {len(report['incidents'])} signals are shown. JSON contains every signal."
        )
    doc.add_heading(tr("Coverage and interpretation"), 1)
    for warning in report["warnings"]:
        doc.add_paragraph(tr(warning))
    if report["excluded"]:
        doc.add_paragraph(
            f"Исключено записей: {len(report['excluded'])}. Причины указаны в JSON."
            if report.get("lang") == "ru"
            else f"{len(report['excluded'])} recordings were excluded because their date, analysis or period coverage was unavailable. Reasons are listed in the JSON report."
        )
    doc.add_heading(tr("Reproducibility"), 1)
    doc.add_paragraph(
        f"Report ID {report.get('id', 'preview')}. Generated {report['created_at']}. The companion JSON includes source runs, model IDs, geometry and review state."
    )
    footer = section.footer.paragraphs[0]
    footer.text = "SENTIENT     "
    footer.runs[0].font.size = Pt(8)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    doc.core_properties.title = report["title"]
    doc.core_properties.author = "Sentient Operations"
    doc.save(str(path))


# Till-roll summary of a day or week. The layout is plain text on purpose: it is
# the document a shift manager would tear off, and it prints on any narrow printer.
Z_TEXT = {
    "en": dict(
        title="FLOOR Z-REPORT",
        simulated="SIMULATED, NOT MEASURED",
        undated="UNDATED RECORDINGS",
        came_in="CAME IN",
        left="LEFT",
        most="MOST IN STORE",
        busiest="BUSIEST HOUR",
        stay="TIME IN STORE",
        queue="QUEUE",
        longest="LONGEST LINE",
        over="OVER THE LIMIT",
        peak_wait="WAIT AT THE PEAK",
        gave_up="GAVE UP WAITING",
        check="PICKUPS AGAINST THE TILL",
        pickups="PICKUP CANDIDATES",
        reached="REACHED A TILL",
        passed="LEFT WITHOUT A TILL",
        lost="TRACK LOST",
        value="VALUE TO REVIEW",
        todo="WHAT TO DO",
        minutes="MIN",
        hours="H",
        note="Review candidates before acting. A pickup without a till is a reason to look, not proof of theft.",
    ),
    "ru": dict(
        title="Z-ОТЧЁТ ЗАЛА",
        simulated="СИМУЛЯЦИЯ, НЕ ИЗМЕРЕНИЯ",
        undated="ЗАПИСИ БЕЗ ДАТЫ",
        came_in="ВОШЛО",
        left="ВЫШЛО",
        most="МАКСИМУМ В ЗАЛЕ",
        busiest="ЧАС ПИК",
        stay="ВРЕМЯ В ЗАЛЕ",
        queue="ОЧЕРЕДЬ",
        longest="САМАЯ ДЛИННАЯ",
        over="ВЫШЕ ЛИМИТА",
        peak_wait="ЖДУТ В ЧАС ПИК",
        gave_up="НЕ ДОЖДАЛИСЬ",
        check="СВЕРКА С КАССОЙ",
        pickups="ПОДБОРЫ",
        reached="ДОШЁЛ ДО КАССЫ",
        passed="МИМО КАССЫ",
        lost="ТРЕК ОБОРВАЛСЯ",
        value="НА ПРОВЕРКЕ",
        todo="ЧТО СДЕЛАТЬ",
        minutes="МИН",
        hours="Ч",
        note="Проверьте запись перед любыми действиями. Подбор мимо кассы повод посмотреть, а не доказательство кражи.",
    ),
}
CODE39 = {
    "0": "nnnwwnwnn",
    "1": "wnnwnnnnw",
    "2": "nnwwnnnnw",
    "3": "wnwwnnnnn",
    "4": "nnnwwnnnw",
    "5": "wnnwwnnnn",
    "6": "nnwwwnnnn",
    "7": "nnnwnnwnw",
    "8": "wnnwnnwnn",
    "9": "nnwwnnwnn",
    "Z": "nwwnwnnnn",
    "*": "nwnnwnwnn",
}
CURRENCY = dict(KZT="₸", USD="$", EUR="€", RUB="₽")


def grouped(value):
    return "—" if value is None else f"{value:,}".replace(",", " ")


def wait_clock(seconds):
    if seconds is None:
        return "—"
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def spent(minutes, text):
    if minutes is None:
        return "—"
    total = round(minutes)
    return f"{total} {text['minutes']}" if total < 60 else f"{total // 60} {text['hours']} {total % 60} {text['minutes']}"


def z_recommendation(item, lang):
    p = item["params"]
    name = RU.get(p.get("zone"), p.get("zone")) if lang == "ru" else p.get("zone")
    hour = lambda h: f"{h:02d}:00"
    if lang == "ru":
        return {
            "review_pickups": lambda: f"Проверить подборы: {p['count']}",
            "open_till": lambda: f"Вторая касса {hour(p['start'])}-{hour(p['end'])}",
            "dwell_no_pickup": lambda: f"Стоят, но не берут: {name}",
            "cold_zone": lambda: f"Мало доходят: {name}",
            "peak_hour": lambda: f"Смена на {hour(p['hour'])}",
            "traffic_change": lambda: f"Трафик {p['delta']:+}%",
        }[item["code"]]()
    return {
        "review_pickups": lambda: f"Review pickups: {p['count']}",
        "open_till": lambda: f"Second till {hour(p['start'])}-{hour(p['end'])}",
        "dwell_no_pickup": lambda: f"Stop but rarely take: {name}",
        "cold_zone": lambda: f"Few reach: {name}",
        "peak_hour": lambda: f"Staff for {hour(p['hour'])}",
        "traffic_change": lambda: f"Footfall {p['delta']:+}%",
    }[item["code"]]()


def build_z_report(data, lang, target):
    """Draw the floor report as an 80 mm till roll."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    text = Z_TEXT["ru" if lang == "ru" else "en"]
    fonts = Path(__file__).parent / "assets/fonts"
    if "SentientMono" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("SentientMono", str(fonts / "DejaVuSansMono.ttf")))
        pdfmetrics.registerFont(TTFont("SentientMonoBold", str(fonts / "DejaVuSansMono-Bold.ttf")))
    k = data["kpis"]
    width, margin, size = 226.8, 13, 7.6
    columns = int((width - margin * 2) / (size * 0.602))
    leading = size + 3.2
    rows = []

    def centre(value, bold=False):
        rows.append(("centre", value, bold))

    def rule():
        rows.append(("rule", "-" * columns, False))

    def line(name, value, bold=False):
        value = str(value)
        dots = "." * max(1, columns - len(name) - len(value) - 2)
        rows.append(("left", f"{name} {dots} {value}", bold))

    def heading(value):
        rows.append(("left", value, True))

    name = "SENTIENT"
    centre(name, True)
    store = data["store"]
    centre(store.get("name", store["id"]).upper())
    centre(text["title"], True)
    if data["mode"] == "calendar":
        centre(data["end"] if data["span_days"] == 1 else f"{data['start']} / {data['end']}")
    else:
        centre(text["undated"])
    if store["simulated"]:
        centre(text["simulated"], True)
    rule()
    line(text["came_in"], grouped(k["entries"]))
    line(text["left"], grouped(k["exits"]))
    line(text["most"], grouped(k["people_peak"]))
    busiest = k["busiest_hour"]
    line(text["busiest"], f"{busiest['hour']:02d}:00" if busiest else "—")
    stay = str(k["visit_minutes"]).replace(".", ",") if lang == "ru" else k["visit_minutes"]
    line(text["stay"], f"{stay} {text['minutes']}" if k["visit_minutes"] is not None else "—")
    if k["queue_peak"] is not None:
        rule()
        heading(text["queue"])
        line(text["longest"], grouped(k["queue_peak"]))
        line(text["over"], spent(k["queue_over_minutes"], text))
        line(text["peak_wait"], wait_clock(k["wait_peak_s"]))
        line(text["gave_up"], grouped(k["abandoned"]))
    if k["pickups"] is not None:
        rule()
        heading(text["check"])
        line(text["pickups"], grouped(k["pickups"]))
        line(text["reached"], grouped(k["reached_checkout"]))
        line(text["passed"], grouped(k["left_without_checkout"]), True)
        line(text["lost"], grouped(k["lost_before_checkout"]))
    risk = data.get("value_at_risk") or {}
    if risk.get("amount") is not None:
        rule()
        line(text["value"], f"{grouped(risk['amount'])} {CURRENCY.get(risk['currency'], risk['currency'])}", True)
    actions = data.get("recommendations") or []
    if actions:
        rule()
        heading(text["todo"])
        for item in actions[:5]:
            for part in textwrap.wrap("- " + z_recommendation(item, lang), columns, subsequent_indent="  "):
                rows.append(("left", part, False))
    rule()
    for part in textwrap.wrap(text["note"], columns):
        rows.append(("left", part, False))
    code = "Z" + (data.get("end") or "0").replace("-", "")
    height = margin * 2 + len(rows) * leading + 54
    pdf = canvas.Canvas(target, pagesize=(width, height))
    pdf.setTitle(text["title"])
    pdf.setAuthor("Sentient")
    y = height - margin - size
    for kind, value, bold in rows:
        pdf.setFont("SentientMonoBold" if bold else "SentientMono", size)
        if kind == "centre":
            pdf.drawCentredString(width / 2, y, value)
        else:
            pdf.drawString(margin, y, value)
        y -= leading
    modules = sum(sum(3 if e == "w" else 1 for e in CODE39[c]) + 1 for c in f"*{code}*")
    unit = (width - margin * 2) / modules
    x = margin
    y -= 30
    for character in f"*{code}*":
        for index, element in enumerate(CODE39[character]):
            size_units = 3 if element == "w" else 1
            if index % 2 == 0:
                pdf.rect(x, y, size_units * unit, 30, stroke=0, fill=1)
            x += size_units * unit
        x += unit
    pdf.setFont("SentientMono", size)
    pdf.drawCentredString(width / 2, y - 10, code)
    pdf.showPage()
    pdf.save()
