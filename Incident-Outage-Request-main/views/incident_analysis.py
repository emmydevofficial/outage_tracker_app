"""Incident Report Analysis -- filter the synced incidents and see the
patterns: volume over time, by station, by incident type, plus
resolution time (date of closure − date of incident). Rendered by
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

ALL_REGIONS = [
    "ABUJA", "BAUCHI", "BENIN", "ENUGU", "KADUNA", "KANO",
    "LAGOS", "OSOGBO", "PORTHARCOURT", "SHIRORO",
]
DATE_FIELDS = {
    "Date of Incident": "date_of_incident",
    "Date of Closure": "date_of_closure",
}

page_header("Incident Report Analysis", "Filter and break down synced incident reports")

if is_admin():
    region_choice = st.selectbox("Region", options=["All Regions"] + ALL_REGIONS, key="ia_region")
    selected_region = None if region_choice == "All Regions" else region_choice
else:
    selected_region = current_region()

df = db.read_incident_reports(region=selected_region)
if df.empty:
    st.info("No incident reports to analyse yet. Pull some on the **Pull Data** page.")
    st.stop()

for f in DATE_FIELDS.values():
    df[f] = pd.to_datetime(df[f], errors="coerce")
df["resolution_days"] = (df["date_of_closure"] - df["date_of_incident"]).dt.days

st.markdown("#### Filters")
today = date.today()
r1, r2, r3 = st.columns([1.4, 1, 1])
with r1:
    date_field_label = st.selectbox("Filter by date", options=list(DATE_FIELDS.keys()), key="ia_date_field")
with r2:
    from_date = st.date_input("From", value=date(today.year, 1, 1), key="ia_from")
with r3:
    to_date = st.date_input("To", value=today, key="ia_to")
date_field = DATE_FIELDS[date_field_label]

f3, f4, f5 = st.columns(3)
with f3:
    station_pick = st.multiselect(
        "Location / Station", options=sorted(df["location_station"].dropna().astype(str).unique()), key="ia_station"
    )
with f4:
    type_pick = st.multiselect(
        "Incident type", options=sorted(df["incident_type"].dropna().astype(str).unique()), key="ia_type"
    )
with f5:
    status_pick = st.multiselect(
        "Status", options=sorted(df["status"].dropna().astype(str).unique()), key="ia_status"
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
    fdf = fdf[fdf["location_station"].astype(str).isin(station_pick)]
if type_pick:
    fdf = fdf[fdf["incident_type"].astype(str).isin(type_pick)]
if status_pick:
    fdf = fdf[fdf["status"].astype(str).isin(status_pick)]

st.divider()
if fdf.empty:
    st.warning("No incident reports match these filters.")
    st.stop()

resolution_valid = fdf["resolution_days"].dropna()
still_open = int(fdf["date_of_closure"].isna().sum())
kpi_grid([
    kpi_card("Incidents", f"{len(fdf)}", "", "alert", TCN_BLUE),
    kpi_card("Load Interrupted", f"{fdf['load_interrupted_mw'].sum():.1f}", "MW", "bolt", TCN_RED),
    kpi_card("Stations Affected", f"{fdf['location_station'].nunique()}", "", "building", TCN_BLUE),
    kpi_card("Avg Resolution Time", f"{resolution_valid.mean():.1f}" if not resolution_valid.empty else "—", "days", "clock", "#956400"),
    kpi_card("Still Open", f"{still_open}", "no closure date", "pulse", TCN_RED),
])

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Incidents per month** (by date of incident)")
    monthly = (
        fdf.dropna(subset=["date_of_incident"])
        .assign(month=lambda d: d["date_of_incident"].dt.to_period("M").astype(str))
        .groupby("month").size().reset_index(name="incidents")
    )
    if not monthly.empty:
        fig = px.bar(monthly, x="month", y="incidents", color_discrete_sequence=[TCN_BLUE])
        fig.update_layout(**TCN_CHART_LAYOUT)
        st.plotly_chart(_style_chart(fig), use_container_width=True)
    else:
        st.caption("No dated rows.")
with c2:
    st.markdown("**Incident type**")
    type_counts = fdf["incident_type"].fillna("Unspecified").value_counts().reset_index()
    type_counts.columns = ["type", "incidents"]
    fig = px.pie(type_counts, names="type", values="incidents", color_discrete_sequence=TCN_COLORS, hole=0.45)
    fig.update_layout(**TCN_CHART_LAYOUT)
    st.plotly_chart(_style_chart(fig), use_container_width=True)

c3, c4 = st.columns(2)
with c3:
    st.markdown("**Top stations by incident count**")
    by_station = fdf.groupby("location_station").size().reset_index(name="incidents").sort_values("incidents", ascending=False).head(15)
    fig = px.bar(by_station, x="incidents", y="location_station", orientation="h", color_discrete_sequence=[TCN_RED])
    fig.update_layout(**TCN_CHART_LAYOUT, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(_style_chart(fig), use_container_width=True)
with c4:
    st.markdown("**Resolution time distribution** (days, date of closure − date of incident)")
    if not resolution_valid.empty:
        fig = px.histogram(resolution_valid, nbins=20, color_discrete_sequence=[TCN_BLUE])
        fig.update_layout(**TCN_CHART_LAYOUT, showlegend=False, xaxis_title="days to close", yaxis_title="incidents")
        st.plotly_chart(_style_chart(fig), use_container_width=True)
        neg = int((resolution_valid < 0).sum())
        if neg:
            st.caption(f"⚠️ {neg} incident(s) have a closure date *before* their incident date — likely data-entry errors worth checking.")
    else:
        st.caption("Not enough closed incidents to compute resolution time.")

st.divider()
st.markdown("#### Filtered rows")
show_cols = {
    "date_of_incident": "Date of Incident", "location_station": "Location / Station",
    "incident_type": "Type", "description": "Description", "equipment_involved": "Equipment",
    "load_interrupted_mw": "Load Interrupted (MW)", "load_interrupted_status": "Load Status",
    "root_cause": "Root Cause", "date_of_closure": "Date of Closure",
    "resolution_days": "Resolution (days)", "status": "Status", "remarks": "Remarks",
}
out = fdf[list(show_cols.keys())].rename(columns=show_cols)
for c in ("Date of Incident", "Date of Closure"):
    out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
st.dataframe(one_indexed(out), use_container_width=True, height=420)
st.caption(f"{len(fdf):,} of {len(df):,} incident(s) match the filters.")
