"""Incident Reports -- the synced rows as a table with live filtering.

Same filter shape as Outage Requests: pick which date to filter on
(date of incident / date of closure), then a From/To range. Counts update
live. Rendered by app.py's router (login/CSS already done)."""
from datetime import date

import pandas as pd
import streamlit as st

import db
from auth import is_admin, current_region
from branding import page_header, one_indexed, kpi_card, kpi_grid, TCN_BLUE, TCN_RED

ALL_REGIONS = [
    "ABUJA", "BAUCHI", "BENIN", "ENUGU", "KADUNA", "KANO",
    "LAGOS", "OSOGBO", "PORTHARCOURT", "SHIRORO",
]
DATE_FIELDS = {
    "Date of Incident": "date_of_incident",
    "Date of Closure": "date_of_closure",
}

page_header("Incident Reports", "Synced incident records — filter and count")

# ── Row 1: Region + which date to filter on ───────────────────────────
c1, c2 = st.columns(2)
with c1:
    if is_admin():
        region_choice = st.selectbox("Region", options=["All Regions"] + ALL_REGIONS, key="ir_region")
        selected_region = None if region_choice == "All Regions" else region_choice
    else:
        selected_region = current_region()
        st.text_input("Region", value=selected_region or "—", disabled=True)
with c2:
    date_field_label = st.selectbox("Filter by date", options=list(DATE_FIELDS.keys()), key="ir_date_field")
date_field = DATE_FIELDS[date_field_label]

# ── Row 2: From / To (always visible, even with no data yet) ──────────
today = date.today()
d1, d2 = st.columns(2)
with d1:
    from_date = st.date_input("From", value=date(today.year, 1, 1), key="ir_from")
with d2:
    to_date = st.date_input("To", value=today, key="ir_to")

st.caption(f"Showing incident reports whose **{date_field_label}** falls between {from_date} and {to_date} (inclusive).")

# ── Data + filter ────────────────────────────────────────────────────
base_df = db.read_incident_reports(region=selected_region)
total_in_db = len(base_df)

if from_date > to_date:
    st.error("'From' is after 'To' — swap them.")
    st.stop()

if base_df.empty:
    kpi_grid([kpi_card("In Database", "0", "", "building", TCN_BLUE)])
    st.info("No incident reports have been pulled for this region yet. Use the **Pull Data** page.")
    st.stop()

for f in DATE_FIELDS.values():
    base_df[f] = pd.to_datetime(base_df[f], errors="coerce")

col = base_df[date_field]
lo, hi = pd.Timestamp(from_date), pd.Timestamp(to_date)
fdf = base_df[(col >= lo) & (col <= hi)]

# ── Counts (live) ────────────────────────────────────────────────────
last_run = db.latest_sync_run(selected_region or "ABUJA", "incident_report")
if last_run:
    icon = {"success": "✅", "running": "⏳"}.get(last_run["status"], "❌")
    st.caption(f"{icon} Last sync for {selected_region or 'ABUJA'}: {last_run['started_at']}")

still_open = int(fdf["date_of_closure"].isna().sum())
kpi_grid([
    kpi_card("In Database", f"{total_in_db}", "", "building", TCN_BLUE),
    kpi_card("Matching Filters", f"{len(fdf)}", "", "alert", TCN_RED),
    kpi_card(
        "Load Interrupted (filtered)",
        f"{fdf['load_interrupted_mw'].sum():.1f}" if not fdf.empty else "0.0",
        "MW", "bolt", TCN_BLUE,
    ),
    kpi_card("Still Open (no closure date)", f"{still_open}", "", "clock", "#956400"),
])

if fdf.empty:
    st.warning(f"No incident reports have a {date_field_label.lower()} between {from_date} and {to_date}.")
    st.stop()

display_cols = {
    "date_of_incident": "Date of Incident", "time_out": "Time Out",
    "location_station": "Location / Station", "incident_type": "Type",
    "description": "Description", "immediate_action_taken": "Immediate Action Taken",
    "affected_stations": "Affected Stations", "equipment_involved": "Equipment Involved",
    "load_interrupted_mw": "Load Interrupted (MW)", "load_interrupted_status": "Load Status",
    "potential_impact": "Potential Impact", "root_cause": "Root Cause",
    "corrective_action": "Corrective Action", "date_of_closure": "Date of Closure",
    "time_in": "Time In", "status": "Status", "remarks": "Remarks",
}
out = fdf[list(display_cols.keys())].rename(columns=display_cols)
for c in ("Date of Incident", "Date of Closure"):
    out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
st.dataframe(one_indexed(out), use_container_width=True, height=520)
st.caption(f"Showing {len(fdf):,} of {total_in_db:,} incident report(s) for this region.")
