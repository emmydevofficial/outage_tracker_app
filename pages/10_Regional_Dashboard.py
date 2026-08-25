import streamlit as st
from utils.auth import login, filter_to_user_region
import pandas as pd
from utils.db import read_outages
from utils.branding import inject_css, page_header, kpi_card, kpi_grid, TCN_COLORS, TCN_CHART_LAYOUT, _style_chart, one_indexed
from datetime import date, timedelta
import plotly.express as px
import tempfile
import os

login()

st.set_page_config(page_title="Regional Dashboard", page_icon="⚡", layout="wide")
inject_css()
page_header("Regional Dashboard", "33kV Feeder Network · Cross-Region Comparison")
st.markdown(
    "Use this dashboard to inspect outage performance by region, compare load loss and outage duration, and review per-region outage metrics."
)

today = date.today()
start_default = today - timedelta(days=30)
start_date, end_date = st.date_input(
    "Select date range",
    value=[start_default, today],
    key="regional_dashboard_dates"
)

out_df = read_outages(str(start_date), str(end_date))
out_df = filter_to_user_region(out_df)
if out_df.empty:
    st.warning("No outage records found for the selected date range.")
    st.stop()

# ensure last_load can be used for load-loss calculations
out_df["last_load"] = pd.to_numeric(out_df["last_load"], errors="coerce")
out_df["start_ts"] = pd.to_datetime(
    out_df["date_off"].astype(str) + " " + out_df["time_off"].astype(str),
    errors="coerce"
)
out_df["end_ts"] = pd.to_datetime(
    out_df["date_on"].astype(str) + " " + out_df["time_on"].astype(str),
    errors="coerce"
)
out_df["duration_min"] = (out_df["end_ts"] - out_df["start_ts"]).dt.total_seconds() / 60.0
out_df["duration_hr"] = out_df["duration_min"] / 60.0
out_df["load_loss_mwh"] = out_df["duration_hr"] * out_df["last_load"]
out_df["party_responsible"] = out_df["party_responsible"].fillna("Unknown")
out_df["outage_class"] = out_df["outage_class"].fillna("Unknown")

available_regions = sorted(out_df["region"].dropna().unique())
selected_regions = st.multiselect(
    "Select region(s)",
    options=available_regions,
    default=available_regions,
    key="regional_dashboard_regions"
)

if not selected_regions:
    st.warning("Select at least one region to view the dashboard.")
    st.stop()

selected_df = out_df[out_df["region"].isin(selected_regions)].copy()

region_summary = selected_df.groupby("region", dropna=False).agg(
    outages_count=("id", "count"),
    total_outage_hours=("duration_hr", "sum"),
    total_load_loss_mwh=("load_loss_mwh", "sum"),
    avg_duration_min=("duration_min", "mean"),
    unique_stations=("station", "nunique"),
    unique_feeders=("feeder_33kv", "nunique")
).reset_index()
region_summary = region_summary.sort_values("total_load_loss_mwh", ascending=False)
region_summary["total_outage_hours"] = region_summary["total_outage_hours"].round(2)
region_summary["total_load_loss_mwh"] = region_summary["total_load_loss_mwh"].round(2)
region_summary["avg_duration_min"] = region_summary["avg_duration_min"].round(1)

total_outage_hours_selected = selected_df["duration_hr"].sum()
total_load_loss_selected = selected_df["load_loss_mwh"].sum()

st.subheader("Regional Summary")
kpi_grid([
    kpi_card("Regions", f"{len(selected_regions)}", "", "building", "#1e3a7a"),
    kpi_card("Outages", f"{int(selected_df['id'].count())}", "", "alert", "#c81e28"),
    kpi_card("Outage Hours", f"{total_outage_hours_selected:.2f}", "hrs", "clock", "#1F6C9F"),
    kpi_card("Load Loss", f"{total_load_loss_selected:.2f}", "MWh", "bolt", "#956400"),
])

st.dataframe(one_indexed(region_summary))

for region in selected_regions:
    region_df = selected_df[selected_df["region"] == region].copy()
    if region_df.empty:
        continue

    region_total_hours = region_df["duration_hr"].sum()
    region_total_load_loss = region_df["load_loss_mwh"].sum()
    region_outages = int(region_df["id"].count())
    region_avg_duration = region_df["duration_min"].mean()
    region_stations = int(region_df["station"].nunique())
    region_feeders = int(region_df["feeder_33kv"].nunique())

    with st.expander(f"{region} Region — Outage details", expanded=len(selected_regions) == 1):
        kpi_grid([
            kpi_card("Outages", f"{region_outages}", "", "alert", "#c81e28"),
            kpi_card("Outage Hours", f"{region_total_hours:.2f}", "hrs", "clock", "#1e3a7a"),
            kpi_card("Load Loss", f"{region_total_load_loss:.2f}", "MWh", "bolt", "#1F6C9F"),
            kpi_card("Avg Duration", f"{region_avg_duration:.1f}", "min", "clock", "#956400"),
        ])

        st.markdown("**Load Loss Summary**")
        load_loss_card = st.info(
            f"{region} total load loss is {region_total_load_loss:.2f} MWh over {region_outages} outages."
        )

        top_stations = (
            region_df.groupby("station", dropna=False)
            .agg(
                outages_count=("id", "count"),
                outage_hours=("duration_hr", "sum"),
                load_loss_mwh=("load_loss_mwh", "sum")
            )
            .reset_index()
            .sort_values("outage_hours", ascending=False)
            .head(10)
        )
        top_feeders = (
            region_df.groupby("feeder_33kv", dropna=False)
            .agg(
                outages_count=("id", "count"),
                outage_hours=("duration_hr", "sum"),
                load_loss_mwh=("load_loss_mwh", "sum")
            )
            .reset_index()
            .sort_values("outage_hours", ascending=False)
            .head(10)
        )
        cause_breakdown = (
            region_df["outage_class"]
            .value_counts(dropna=False)
            .rename_axis("outage_class")
            .reset_index(name="count")
        )
        party_breakdown = (
            region_df["party_responsible"]
            .value_counts(dropna=False)
            .rename_axis("party_responsible")
            .reset_index(name="count")
        )

        station_fig = px.bar(
            top_stations,
            x="station",
            y="outage_hours",
            title=f"Top Stations by Outage Hours — {region}",
            labels={"outage_hours": "Outage Hours"},
            color_discrete_sequence=TCN_COLORS,
        )
        feeder_fig = px.bar(
            top_feeders,
            x="feeder_33kv",
            y="outage_hours",
            title=f"Top Feeders by Outage Hours — {region} Region",
            labels={"outage_hours": "Outage Hours"},
            color_discrete_sequence=TCN_COLORS,
        )
        cause_fig = px.pie(
            cause_breakdown,
            names="outage_class",
            values="count",
            title=f"Outage Cause Breakdown — {region} Region",
            color_discrete_sequence=TCN_COLORS,
        )
        party_fig = px.pie(
            party_breakdown,
            names="party_responsible",
            values="count",
            title=f"Party Responsible Breakdown — {region} Region",
            color_discrete_sequence=TCN_COLORS,
        )

        for _f in (station_fig, feeder_fig, cause_fig, party_fig):
            _f.update_layout(**TCN_CHART_LAYOUT)
            _style_chart(_f)

        st.plotly_chart(station_fig, use_container_width=True)
        st.plotly_chart(feeder_fig, use_container_width=True)
        st.plotly_chart(cause_fig, use_container_width=True)
        st.plotly_chart(party_fig, use_container_width=True)

        st.subheader("Top station outage details")
        st.dataframe(one_indexed(top_stations))
        st.subheader("Top feeder outage details")
        st.dataframe(one_indexed(top_feeders))
