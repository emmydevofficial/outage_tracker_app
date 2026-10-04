"""One-off: a PDF list of TCN-attributed outages that have no restoration
date/time logged at all (still open), as of today -- for sending up the
chain, independent of any report run. Not wired into the Streamlit page;
run directly.

Run from outage_tracker-main/: ./venv/bin/python reliability_report/still_open_pdf.py
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from sqlalchemy import text
from utils.db import get_engine

OUT = Path(__file__).resolve().parent.parent / "data" / "samples" / "output" / "TCN_Still_Open_Outages.pdf"


def main(as_of: dt.date | None = None):
    as_of = as_of or dt.date.today()
    engine = get_engine()
    with engine.connect() as con:
        rows = con.execute(text("""
            SELECT region, station, feeder_33kv, date_off, time_off, outage_class, remarks
            FROM outages
            WHERE date_off <= :as_of AND date_on IS NULL AND party_responsible = 'TCN'
            ORDER BY date_off, time_off
        """), dict(as_of=as_of)).fetchall()

    doc = SimpleDocTemplate(str(OUT), pagesize=landscape(A4),
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Heading1"], fontSize=18, textColor=colors.HexColor("#1a3a6b"))
    story = [
        Paragraph("TCN 33kV Feeders Currently Open (No Restoration Logged)", title_style),
        Spacer(1, 0.3 * cm),
        Paragraph(f"As of {as_of:%d %B %Y} -- outages attributed to TCN with no date/time of restoration recorded.", styles["Normal"]),
        Paragraph(f"Total: {len(rows)} feeders.", styles["Normal"]),
        Spacer(1, 0.5 * cm),
    ]

    header = ["Region", "Station", "Feeder", "Opened", "Days Open", "Class", "Remarks"]
    data = [header]
    for r in rows:
        opened = dt.datetime.combine(r.date_off, r.time_off) if r.time_off else dt.datetime.combine(r.date_off, dt.time())
        days_open = (as_of - r.date_off).days
        remarks = (r.remarks or "")[:160]
        data.append([r.region, r.station, r.feeder_33kv, opened.strftime("%d %b %Y %H:%M"),
                    str(days_open), r.outage_class or "", remarks])

    col_widths = [2.0 * cm, 3.3 * cm, 3.0 * cm, 3.2 * cm, 1.8 * cm, 2.0 * cm, 8.5 * cm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3a6b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5fa")]),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "Note: these outages are excluded from the SLA exceedance/cost figures until a restoration "
        "date and time is logged -- they are not included in any cost total shown elsewhere.",
        styles["Normal"]))

    doc.build(story)
    print(f"Wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
