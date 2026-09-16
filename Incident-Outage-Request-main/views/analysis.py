"""Outage Request Analysis -- filter the synced requests and see the
patterns: volume over time, by station, by DISCO, by inferred work
category, plus lead-time (how far ahead requests are filed). Rendered by
app.py's router (login/CSS done)."""
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import db
from auth import is_admin, current_region
from branding import (
    page_header, one_indexed, kpi_card, kpi_grid,
    TCN_COLORS, TCN_CHART_LAYOUT, _style_chart, TCN_BLUE, TCN_RED,
)
from sheet_sync import classify_work

ALL_REGIONS = [
    "ABUJA", "BAUCHI", "BENIN", "ENUGU", "KADUNA", "KANO",
    "LAGOS", "OSOGBO", "PORTHARCOURT", "SHIRORO",
]
DATE_FIELDS = {
    "Application Date": "application_date",
    "Scheduled Start Date": "scheduled_start_date",
    "Scheduled End Date": "scheduled_end_date",
}

page_header("Outage Request Analysis", "Filter and break down synced outage requests")

if is_admin():
    region_choice = st.selectbox("Region", options=["All Regions"] + ALL_REGIONS, key="an_region")
    selected_region = None if region_choice == "All Regions" else region_choice
else:
    selected_region = current_region()

df = db.read_outage_requests(region=selected_region)
if df.empty:
    st.info("No outage requests to analyse yet. Pull some on the **Pull Data** page.")
    st.stop()

for f in DATE_FIELDS.values():
    df[f] = pd.to_datetime(df[f], errors="coerce")
df["work_category"] = df.apply(
    lambda r: classify_work(r.get("description_of_work"), r.get("remarks")), axis=1
)
df["lead_time_days"] = (df["scheduled_start_date"] - df["application_date"]).dt.days

st.markdown("#### Filters")
today = date.today()
r1, r2, r3 = st.columns([1.4, 1, 1])
with r1:
    date_field_label = st.selectbox("Filter by date", options=list(DATE_FIELDS.keys()), key="an_date_field")
with r2:
    from_date = st.date_input("From", value=date(today.year, 1, 1), key="an_from")
with r3:
    to_date = st.date_input("To", value=today, key="an_to")
date_field = DATE_FIELDS[date_field_label]

f3, f4, f5 = st.columns(3)
with f3:
    station_pick = st.multiselect(
        "Station", options=sorted(df["station"].dropna().astype(str).unique()), key="an_station"
    )
with f4:
    disco_pick = st.multiselect(
        "DISCO", options=sorted(df["disco_affected"].dropna().astype(str).unique()), key="an_disco"
    )
with f5:
    cat_pick = st.multiselect(
        "Work category (inferred from description)",
        options=["Planned", "Emergency", "Other"], key="an_cat",
    )

st.caption(f"Date filter on **{date_field_label}**, {from_date} to {to_date} (inclusive).")

if from_date > to_date:
    st.error("'From' is after 'To' — swap them.")
    st.stop()

fdf = df.copy()
col = fdf[date_field]
lo, hi = pd.Timestamp(from_date), pd.Timestamp(to_date)
fdf = fdf[(col >= lo) & (col <= hi)]
if station_pick:
    fdf = fdf[fdf["station"].astype(str).isin(station_pick)]
if disco_pick:
    fdf = fdf[fdf["disco_affected"].astype(str).isin(disco_pick)]
if cat_pick:
    fdf = fdf[fdf["work_category"].isin(cat_pick)]

st.divider()
if fdf.empty:
    st.warning("No requests match these filters.")
    st.stop()

lead_valid = fdf["lead_time_days"].dropna()
same_day = int((lead_valid <= 0).sum())
kpi_grid([
    kpi_card("Requests", f"{len(fdf)}", "", "alert", TCN_BLUE),
    kpi_card("Total Load Affected", f"{fdf['expected_load_mw'].sum():.1f}", "MW", "bolt", TCN_RED),
    kpi_card("Stations", f"{fdf['station'].nunique()}", "", "building", TCN_BLUE),
    kpi_card("Avg Lead Time", f"{lead_valid.mean():.1f}" if not lead_valid.empty else "—", "days", "clock", "#956400"),
    kpi_card("Filed Same/Next-Day", f"{same_day}" if not lead_valid.empty else "—", f"of {len(lead_valid)}", "pulse", TCN_RED),
])

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Requests per month** (by scheduled start)")
    monthly = (
        fdf.dropna(subset=["scheduled_start_date"])
        .assign(month=lambda d: d["scheduled_start_date"].dt.to_period("M").astype(str))
        .groupby("month").size().reset_index(name="requests")
    )
    if not monthly.empty:
        fig = px.bar(monthly, x="month", y="requests", color_discrete_sequence=[TCN_BLUE])
        fig.update_layout(**TCN_CHART_LAYOUT)
        st.plotly_chart(_style_chart(fig), use_container_width=True)
    else:
        st.caption("No dated rows.")
with c2:
    st.markdown("**Work category** (inferred)")
    cat_counts = fdf["work_category"].value_counts().reset_index()
    cat_counts.columns = ["category", "requests"]
    fig = px.pie(cat_counts, names="category", values="requests", color_discrete_sequence=TCN_COLORS, hole=0.45)
    fig.update_layout(**TCN_CHART_LAYOUT)
    st.plotly_chart(_style_chart(fig), use_container_width=True)

c3, c4 = st.columns(2)
with c3:
    st.markdown("**Top stations by request count**")
    by_station = fdf.groupby("station").size().reset_index(name="requests").sort_values("requests", ascending=False).head(15)
    fig = px.bar(by_station, x="requests", y="station", orientation="h", color_discrete_sequence=[TCN_RED])
    fig.update_layout(**TCN_CHART_LAYOUT, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(_style_chart(fig), use_container_width=True)
with c4:
    st.markdown("**Load affected by DISCO** (MW)")
    by_disco = (
        fdf.dropna(subset=["disco_affected"])
        .groupby("disco_affected")["expected_load_mw"].sum().reset_index()
        .sort_values("expected_load_mw", ascending=False)
    )
    if not by_disco.empty:
        fig = px.bar(by_disco, x="disco_affected", y="expected_load_mw", color_discrete_sequence=[TCN_BLUE])
        fig.update_layout(**TCN_CHART_LAYOUT)
        st.plotly_chart(_style_chart(fig), use_container_width=True)
    else:
        st.caption("No DISCO data.")

st.markdown("**Lead time distribution** (scheduled start − application date, in days)")
if not lead_valid.empty:
    fig = px.histogram(lead_valid, nbins=20, color_discrete_sequence=[TCN_BLUE])
    fig.update_layout(**TCN_CHART_LAYOUT, showlegend=False, xaxis_title="days ahead", yaxis_title="requests")
    st.plotly_chart(_style_chart(fig), use_container_width=True)
    neg = int((lead_valid < 0).sum())
    if neg:
        st.caption(f"⚠️ {neg} request(s) have a scheduled start *before* their application date — likely data-entry errors worth checking.")
else:
    st.caption("Not enough dated rows to compute lead time.")

st.divider()
st.markdown("#### Filtered rows")
show_cols = {
    "application_date": "Application Date", "scheduled_start_date": "Scheduled Start",
    "station": "Station", "voltage_level": "Voltage", "equipment_name": "Equipment",
    "description_of_work": "Description", "work_category": "Category (inferred)",
    "estimated_duration_raw": "Duration", "expected_load_mw": "Load (MW)",
    "expected_load_status": "Load Status", "disco_affected": "DISCO",
    "lead_time_days": "Lead Time (days)", "applicant_name": "Applicant", "remarks": "Remarks",
}
out = fdf[list(show_cols.keys())].rename(columns=show_cols)
for c in ("Application Date", "Scheduled Start"):
    out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
st.dataframe(one_indexed(out), use_container_width=True, height=420)
st.caption(f"{len(fdf):,} of {len(df):,} request(s) match the filters.")
