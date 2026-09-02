"""
### FILE: pages/20_Daily_Outage_Report.py
Narrative "short story" style Daily Outage Report -- pick a date, get a
prose summary of every event that happened that day (region-scoped), plus
a carry-over list of outages still open from any earlier day. Generated
entirely from templates/rules over the outages table, no LLM -- see
utils/narrative_report.py for the generation logic.
"""

import tempfile
import os
from datetime import date

import streamlit as st

from utils.auth import login, is_super_admin, current_region
from utils.branding import inject_css, page_header
from utils.db import read_outages_for_report, read_open_outages, read_distinct_party_responsible
from utils.narrative_report import (
    build_at_a_glance,
    build_highlights,
    build_region_sections,
    build_still_open_section,
)
from utils.report_generator import generate_narrative_word_report, generate_narrative_pdf_report

login()

st.set_page_config(page_title="Daily Outage Report", page_icon="📰", layout="wide")
inject_css()
page_header("Daily Outage Report", "33kV Feeder Network · Narrative Summary")
st.markdown("Pick a date to generate a short-story style summary of that day's outages.")

col1, col2, col3 = st.columns(3)

with col1:
    report_date = st.date_input("Report date", value=date.today(), key="narrative_report_date")

with col2:
    if is_super_admin():
        with st.spinner("Loading regions..."):
            _preview = read_outages_for_report(str(report_date), str(report_date))
        region_options = ["All Regions"] + sorted(_preview["region"].dropna().unique()) if not _preview.empty else ["All Regions"]
        selected_region = st.selectbox("Region", options=region_options, key="narrative_report_region")
    else:
        selected_region = current_region()
        st.text_input("Region", value=selected_region or "—", disabled=True)

with col3:
    party_options = ["All"] + read_distinct_party_responsible()
    default_party_idx = party_options.index("TCN") if "TCN" in party_options else 0
    selected_party = st.selectbox(
        "Party Responsible", options=party_options, index=default_party_idx, key="narrative_report_party"
    )

with st.spinner("Generating report..."):
    day_df = read_outages_for_report(str(report_date), str(report_date))
    open_df = read_open_outages()

if not is_super_admin():
    day_df = day_df[day_df["region"].astype(str).str.strip().str.upper() == str(selected_region).strip().upper()]
    open_df = open_df[open_df["region"].astype(str).str.strip().str.upper() == str(selected_region).strip().upper()]
elif selected_region != "All Regions":
    day_df = day_df[day_df["region"] == selected_region]
    open_df = open_df[open_df["region"] == selected_region]

if selected_party != "All":
    day_df = day_df[day_df["party_responsible"] == selected_party]
    open_df = open_df[open_df["party_responsible"] == selected_party]

at_a_glance = build_at_a_glance(day_df, open_df)
highlights = build_highlights(day_df, top_n=4)
region_sections = build_region_sections(day_df)
still_open_lines = build_still_open_section(open_df)

st.divider()
st.subheader("📊 At a Glance")

class_counts = at_a_glance["class_counts"]
class_line = ", ".join(f"**{k}:** {v}" for k, v in class_counts.items()) or "No events recorded"
c1, c2, c3 = st.columns(3)
c1.metric("Total Events", at_a_glance["total_events"])
c2.metric("Regions Covered", len(at_a_glance["regions_covered"]))
c3.metric("Still Open (all dates)", at_a_glance["still_open_count"])
st.markdown(f"**Breakdown:** {class_line}")
st.markdown(f"**Lowest Recorded Frequency:** {at_a_glance['lowest_frequency']}")
st.markdown(f"**New Assets Energised:** {at_a_glance['new_assets_energised']}")

st.divider()
st.subheader("📰 System Observations")
if highlights:
    for h in highlights:
        st.markdown(f"- {h}")
else:
    st.info("No notable events recorded for this date.")

st.divider()
st.subheader("🗺️ Region-by-Region Summary")
if region_sections:
    for region, stations in region_sections.items():
        with st.expander(f"**{region}**", expanded=True):
            for station, bullets in stations.items():
                st.markdown(f"**{station}**")
                for bullet in bullets:
                    st.markdown(f"- {bullet}")
else:
    st.info("No outage events recorded for this date.")

st.divider()
st.subheader("⚠️ Outages Still Open (Carried Over)")
if still_open_lines:
    for line in still_open_lines:
        st.markdown(f"- {line}")
else:
    st.success("No outages currently open.")

st.divider()
_region_label = selected_region if selected_region and selected_region != "All Regions" else "All Regions"
_party_label = f" · Party Responsible: {selected_party}" if selected_party != "All" else ""
subtitle = f"{_region_label} · 33kV Feeder Network{_party_label}"
dl_col1, dl_col2 = st.columns(2)
with tempfile.TemporaryDirectory() as tmpdir:
    word_filename = f"Daily_Outage_Report_{report_date}.docx"
    word_path = os.path.join(tmpdir, word_filename)
    try:
        generate_narrative_word_report(
            report_date=str(report_date),
            at_a_glance=at_a_glance,
            highlights=highlights,
            region_sections=region_sections,
            still_open_lines=still_open_lines,
            output_path=word_path,
            subtitle=subtitle,
        )
        with open(word_path, "rb") as f:
            dl_col1.download_button(
                label="📥 Download as Word (.docx)",
                data=f.read(),
                file_name=word_filename,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
    except Exception as e:
        dl_col1.error(f"Error generating Word report: {str(e)}")

    pdf_filename = f"Daily_Outage_Report_{report_date}.pdf"
    pdf_path = os.path.join(tmpdir, pdf_filename)
    try:
        generate_narrative_pdf_report(
            report_date=str(report_date),
            at_a_glance=at_a_glance,
            highlights=highlights,
            region_sections=region_sections,
            still_open_lines=still_open_lines,
            output_path=pdf_path,
            subtitle=subtitle,
        )
        with open(pdf_path, "rb") as f:
            dl_col2.download_button(
                label="📄 Download as PDF",
                data=f.read(),
                file_name=pdf_filename,
                mime="application/pdf",
            )
    except Exception as e:
        dl_col2.error(f"Error generating PDF report: {str(e)}")
