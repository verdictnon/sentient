"""Generate durable management reports from an immutable, measured report snapshot."""

from pathlib import Path
from xml.sax.saxutils import escape


def label(value):
    return str(value if value is not None else "Unavailable")


def duration(value):
    if value is None:
        return "Unavailable"
    return (
        f"{value / 3600:.2f} h"
        if value >= 3600
        else f"{value / 60:.1f} min"
        if value >= 60
        else f"{value:.1f} s"
    )


def period(report):
    p = report["period"]
    return (
        "Selected recording collection"
        if report["scope"] == "collection"
        else f"{p['start']} to {p['end']} ({p['timezone']})"
    )


def opening(report):
    s = report["summary"]
    n = len(report["recordings"])
    if not n:
        return "No completed analyses match the selected period and recordings. Store activity cannot be assessed from this report because analysed coverage is unavailable."
    return f"This report covers {n} analysed recordings with {s['tracks']} camera-local observed track IDs and {label(s['pickups'])} pickup candidates. There are {s['pending']} pending signals for operator review. Track IDs are not a deduplicated customer count."


def sections(report):
    recordings = [["Recording and camera", "Tracks", "Pickups", "Observed time"]]
    for r in report["recordings"]:
        recordings.append(
            [
                r["name"] + "\n" + r["camera_id"],
                r["summary"]["tracks"],
                r["summary"]["pickups"],
                duration(r["summary"]["observed_seconds"]),
            ]
        )
    areas = [["Area and camera", "Tracks", "Visits", "Observed dwell", "Pickups"]]
    for a in sorted(report["areas"], key=lambda a: -a["unique_tracks"]):
        areas.append(
            [
                a["name"] + "\n" + a["camera_id"],
                a["unique_tracks"],
                a["visits"],
                duration(a["observed_dwell_seconds"]),
                a["pickup_alerts"],
            ]
        )
    incidents = [["Signal and source time", "Camera", "Review", "Operator notes"]]
    for e in report["incidents"][:50]:
        incidents.append(
            [
                e["name"] + f"\n{e['media_ms'] / 1000:.1f} s",
                e["camera_id"],
                e["review"]["status"],
                e["review"].get("notes", "") or "No notes",
            ]
        )
    dates = [
        ["Recording date", "Track IDs present", "Pickup candidates", "Recordings"]
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
            textColor=colors.HexColor("#52715a"),
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
            name="MutedS",
            fontName=font,
            fontSize=8,
            leading=12,
            textColor=colors.HexColor("#61736a"),
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

    def para(text, style="BodyS"):
        return Paragraph(escape(label(text)).replace("\n", "<br/>"), styles[style])

    story = [
        para("SENTIENT   /   OPERATIONS", "BrandS"),
        para(report["title"], "TitleS"),
        para(period(report), "MutedS"),
        para(opening(report)),
    ]
    s = report["summary"]
    story.append(
        para(
            f"Observed footage: {duration(s['observed_seconds'])}. Peak simultaneous observed tracks in one view: {s['peak_people']}. Area entries: {s['area_visits']}. Accepted signals: {s['accepted']}; dismissed: {s['rejected']}."
        )
    )
    if report.get("notes"):
        story.extend([para("Operator summary", "HeadS"), para(report["notes"])])

    def table(title, rows, widths):
        story.append(para(title, "HeadS"))
        if len(rows) < 2:
            story.append(
                para("No measured results available for this section.", "MutedS")
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
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#233d32")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f2f5ef")],
                    ),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9ded7")),
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
    table("Recording coverage", recordings, [240, 60, 60, 108])
    table("Area performance", areas, [215, 55, 45, 88, 65])
    table("Activity by recording date", dates, [150, 110, 110, 98])
    table("Signals and operator review", incidents, [160, 95, 65, 148])
    if len(report["incidents"]) > 50:
        story.append(
            para(
                f"The first 50 of {len(report['incidents'])} signals are shown. The JSON report contains every signal.",
                "MutedS",
            )
        )
    story.append(para("Coverage and interpretation", "HeadS"))
    for warning in report["warnings"]:
        story.append(para(warning))
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
    story.append(para("Reproducibility", "HeadS"))
    story.append(
        para(
            f"Report ID: {report.get('id', 'preview')}. Generated: {report['created_at']}. The companion JSON contains run IDs, model IDs, saved geometry revisions and the full review snapshot. Area CSV is provided separately.",
            "MutedS",
        )
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#d9e0d5"))
        canvas.line(72, 47, 540, 47)
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor("#6b7b71"))
        canvas.drawString(72, 34, "SENTIENT / Recorded-video operations")
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
        style.font.color.rgb = RGBColor.from_string("233D32")
        style.paragraph_format.space_before = Pt(16)
        style.paragraph_format.space_after = Pt(8)
    doc.add_paragraph("SENTIENT   /   OPERATIONS").runs[0].font.size = Pt(9)
    doc.add_paragraph(report["title"], style="Title")
    doc.add_paragraph(period(report))
    doc.add_paragraph(opening(report))
    s = report["summary"]
    doc.add_paragraph(
        f"Observed footage {duration(s['observed_seconds'])}. Peak observed tracks in one view {s['peak_people']}. Area entries {s['area_visits']}. Accepted signals {s['accepted']}. Dismissed signals {s['rejected']}."
    )
    if report.get("notes"):
        doc.add_heading("Operator summary", 1)
        doc.add_paragraph(report["notes"])

    def table(heading, rows, widths):
        doc.add_heading(heading, 1)
        if len(rows) < 2:
            doc.add_paragraph("No measured results available for this section.")
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
                cell.text = label(value)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                props = cell._tc.get_or_add_tcPr()
                shading = OxmlElement("w:shd")
                shading.set(
                    qn("w:fill"),
                    "233D32" if i == 0 else "F2F5EF" if i % 2 == 0 else "FFFFFF",
                )
                props.append(shading)
                borders = OxmlElement("w:tcBorders")
                for edge in ("top", "left", "bottom", "right"):
                    el = OxmlElement("w:" + edge)
                    el.set(qn("w:val"), "single")
                    el.set(qn("w:sz"), "4")
                    el.set(qn("w:color"), "D9D9D9")
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
                            RGBColor(255, 255, 255) if i == 0 else RGBColor(30, 45, 36)
                        )
            if i == 0:
                repeat = OxmlElement("w:tblHeader")
                t.rows[0]._tr.get_or_add_trPr().append(repeat)
        doc.add_paragraph().paragraph_format.space_after = Pt(1)

    recordings, areas, incidents, dates = sections(report)
    table("Recording coverage", recordings, [3.25, 0.8, 0.8, 1.65])
    table("Area performance", areas, [3.1, 0.6, 0.6, 1.25, 0.95])
    table("Activity by recording date", dates, [2, 1.5, 1.5, 1.5])
    table("Signals and operator review", incidents, [2.2, 1.3, 0.9, 2.1])
    if len(report["incidents"]) > 50:
        doc.add_paragraph(
            f"The first 50 of {len(report['incidents'])} signals are shown. JSON contains every signal."
        )
    doc.add_heading("Coverage and interpretation", 1)
    for warning in report["warnings"]:
        doc.add_paragraph(warning)
    if report["excluded"]:
        doc.add_paragraph(
            f"{len(report['excluded'])} recordings were excluded because their date, analysis or period coverage was unavailable. Reasons are listed in the JSON report."
        )
    doc.add_heading("Reproducibility", 1)
    doc.add_paragraph(
        f"Report ID {report.get('id', 'preview')}. Generated {report['created_at']}. The companion JSON includes source runs, model IDs, geometry and review state."
    )
    footer = section.footer.paragraphs[0]
    footer.text = "SENTIENT / Recorded-video operations     "
    footer.runs[0].font.size = Pt(8)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    doc.core_properties.title = report["title"]
    doc.core_properties.author = "Sentient Operations"
    doc.save(str(path))
