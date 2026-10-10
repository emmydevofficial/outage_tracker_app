"""
### FILE: views/feeder_daily_energy.py
"Feeder Daily Energy & Value" -- daily energy delivered per feeder from
the uploaded daily workbooks (feeder_daily / feeder_meter_reading), its
value at DisCo tariff, and forecast vs delivered energy. Entirely
additive: reads feeder_forecast tables plus the same SLA/tariff tables
reliability_report/data.py already reads, writes nothing.
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import date, timedelta

from reliability_report.data import (
    normalize_disco, normalize_feeder, read_default_tariff, read_sla,
    read_tariff_rates, sla_lookup, tariff_lookup, get_rate, DEFAULT_BAND,
)
from utils.auth import filter_to_user_region
from utils.branding import TCN_CHART_LAYOUT, TCN_COLORS, _style_chart, kpi_card, kpi_grid, one_indexed, page_header
from utils.db import get_engine
from utils.regions import REGIONS

page_header("Feeder Daily Energy & Value", "33kV Feeder Network · Daily Energy Delivered & DisCo Tariff Value")
st.caption(
    "Daily energy per feeder from the uploaded daily workbooks, its value at DisCo tariff, and forecast vs "
    "delivered energy. \"Value of energy delivered\" is priced at the DisCo tariff -- it is not a TCN cost figure."
)

engine = get_engine()

today = date.today()
start_default = today - timedelta(days=30)
date_range = st.date_input("Select date range", value=[start_default, today], key="fde_dates")
if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
    st.info("Pick a start and end date.")
    st.stop()
start_date, end_date = date_range

with engine.connect() as con:
    daily = pd.read_sql_query(text("""
        SELECT fd.feeder_id, fd.reading_date, fd.region, fd.disco_seen, fd.feeder_band_seen,
               fd.opening_meter_mwh, fd.closing_meter_mwh, fd.daily_energy_mwh,
               f.station, f.station_norm, f.disco AS registry_disco, f.feeder_band AS registry_band,
               f.canonical_name AS feeder
        FROM feeder_daily fd
        JOIN feeder f ON f.feeder_id = fd.feeder_id
        WHERE fd.reading_date BETWEEN :a AND :b
    """), con, params={"a": start_date, "b": end_date})

if daily.empty:
    st.warning("No daily feeder energy data uploaded for this range yet -- see Upload Daily Feeder Workbook.")
    st.stop()

daily = filter_to_user_region(daily)
if daily.empty:
    st.warning("No daily feeder energy data for your region in this range.")
    st.stop()

col1, col2, col3, col4, col5 = st.columns(5)
region_sel = col1.selectbox("Region", ["All"] + sorted(daily["region"].dropna().unique()))
if region_sel != "All":
    daily = daily[daily["region"] == region_sel]
disco_options = sorted(daily["disco_seen"].fillna(daily["registry_disco"]).dropna().unique())
disco_sel = col2.selectbox("Disco", ["All"] + disco_options)
if disco_sel != "All":
    daily = daily[(daily["disco_seen"] == disco_sel) | (daily["disco_seen"].isna() & (daily["registry_disco"] == disco_sel))]
station_sel = col3.selectbox("Station", ["All"] + sorted(daily["station"].dropna().unique()))
if station_sel != "All":
    daily = daily[daily["station"] == station_sel]
feeder_sel = col4.selectbox("Feeder", ["All"] + sorted(daily["feeder"].dropna().unique()))
if feeder_sel != "All":
    daily = daily[daily["feeder"] == feeder_sel]
band_options = sorted(daily["feeder_band_seen"].fillna(daily["registry_band"]).dropna().unique())
band_sel = col5.selectbox("Band", ["All"] + band_options)
if band_sel != "All":
    daily = daily[(daily["feeder_band_seen"] == band_sel) | (daily["feeder_band_seen"].isna() & (daily["registry_band"] == band_sel))]

if daily.empty:
    st.warning("No rows match these filters.")
    st.stop()

feeder_ids = sorted(daily["feeder_id"].unique().tolist())
with engine.connect() as con:
    actual_check = pd.read_sql_query(text("""
        SELECT feeder_id, source_date AS reading_date, SUM(actual_mwh) AS actual_sum,
               bool_or(flag IN ('R32', 'R33', 'R35')) AS has_flag
        FROM feeder_meter_reading
        WHERE feeder_id = ANY(:fids) AND source_date BETWEEN :a AND :b AND source_label >= 1
        GROUP BY feeder_id, source_date
    """), con, params={"fids": feeder_ids, "a": start_date, "b": end_date})
    forecast_sum = pd.read_sql_query(text("""
        SELECT feeder_id, slot_date AS reading_date, SUM(forecast_mw) AS forecast_mwh
        FROM feeder_hourly_forecast
        WHERE feeder_id = ANY(:fids) AND slot_date BETWEEN :a AND :b
        GROUP BY feeder_id, slot_date
    """), con, params={"fids": feeder_ids, "a": start_date, "b": end_date})

daily = daily.merge(actual_check, on=["feeder_id", "reading_date"], how="left")
daily = daily.merge(forecast_sum, on=["feeder_id", "reading_date"], how="left")
daily["has_flag"] = daily["has_flag"].fillna(False).astype(bool)
daily["actual_sum"] = pd.to_numeric(daily["actual_sum"], errors="coerce")
daily["cross_check_diff"] = (daily["daily_energy_mwh"] - daily["actual_sum"]).abs()
daily["cross_check_mismatch"] = daily["cross_check_diff"] > 0.01

# --- band and rate (section 7's exact fallback: SLA band -> workbook band -> Band A) ---
sla_map = sla_lookup(read_sla())
tariff_map = tariff_lookup(read_tariff_rates())
default_rate = read_default_tariff()


def _band_and_source(row) -> tuple[str, str]:
    hit = sla_map.get((row["station_norm"], normalize_feeder(row["feeder"])))
    if hit:
        return hit[0], "sla"
    if row["feeder_band_seen"]:
        return row["feeder_band_seen"], "workbook"
    return DEFAULT_BAND, "assumed"


bands = daily.apply(_band_and_source, axis=1, result_type="expand")
daily["band_used"], daily["band_source"] = bands[0], bands[1]
daily["disco_norm"] = daily.apply(lambda r: normalize_disco(r["disco_seen"] or r["registry_disco"], r["station"]), axis=1)
daily["rate_used"] = daily.apply(lambda r: get_rate(r["disco_norm"], r["band_used"], tariff_map, default_rate), axis=1)
daily["rate_source"] = daily.apply(
    lambda r: "disco_band" if (r["disco_norm"], r["band_used"]) in tariff_map else "default_rate", axis=1)

priced = daily[~daily["has_flag"]].copy()
excluded = daily[daily["has_flag"]].copy()
priced["value_ngn"] = priced["daily_energy_mwh"].fillna(0) * 1000 * priced["rate_used"]

kpi_grid([
    kpi_card("Feeder-Days", f"{len(daily)}", "", "bolt", "#1e3a7a"),
    kpi_card("Total Energy Delivered", f"{priced['daily_energy_mwh'].sum():.2f}", "MWh", "bolt", "#1e3a7a"),
    kpi_card("Total Value (DisCo Tariff)", f"₦{priced['value_ngn'].sum():,.2f}", "", "chart", "#c81e28"),
])
kpi_grid([
    kpi_card("Flagged / Excluded Days", f"{len(excluded)}", "", "alert", "#c81e28"),
    kpi_card("Cross-Check Mismatches", f"{int(priced['cross_check_mismatch'].sum())}", "", "alert", "#956400"),
    kpi_card("Total Forecast Energy", f"{daily['forecast_mwh'].fillna(0).sum():.2f}", "MWh", "pulse", "#1F6C9F"),
])

st.subheader("Daily totals by region")
region_totals = priced.groupby("region").agg(
    energy_mwh=("daily_energy_mwh", "sum"), value_ngn=("value_ngn", "sum"), feeder_days=("feeder_id", "count"),
).reset_index().sort_values("energy_mwh", ascending=False)
st.dataframe(one_indexed(region_totals), use_container_width=True, column_config={
    "energy_mwh": st.column_config.NumberColumn("Energy (MWh)", format="%.2f"),
    "value_ngn": st.column_config.NumberColumn("Value (₦)", format="₦ %,.2f"),
})
fig = px.bar(region_totals, x="region", y="energy_mwh", title="Energy delivered by region (MWh)",
            color_discrete_sequence=TCN_COLORS)
fig.update_layout(**TCN_CHART_LAYOUT)
_style_chart(fig)
st.plotly_chart(fig, use_container_width=True)

st.subheader("Top feeders by energy and value")
top_n = st.slider("Show top N feeders", 5, 50, 20)
feeder_totals = priced.groupby(["station", "feeder"]).agg(
    energy_mwh=("daily_energy_mwh", "sum"), value_ngn=("value_ngn", "sum"),
    forecast_mwh=("forecast_mwh", "sum"), feeder_days=("feeder_id", "count"),
).reset_index().sort_values("value_ngn", ascending=False).head(top_n)
st.dataframe(one_indexed(feeder_totals), use_container_width=True, column_config={
    "energy_mwh": st.column_config.NumberColumn("Delivered (MWh)", format="%.2f"),
    "forecast_mwh": st.column_config.NumberColumn("Forecast (MWh)", format="%.2f"),
    "value_ngn": st.column_config.NumberColumn("Value (₦)", format="₦ %,.2f"),
})

st.subheader("Feeder trend")
trend_feeder = st.selectbox("Feeder", sorted(daily["feeder"].unique()), key="fde_trend_feeder")
trend_df = daily[daily["feeder"] == trend_feeder].sort_values("reading_date")
fig2 = px.line(trend_df, x="reading_date", y=["daily_energy_mwh", "forecast_mwh"],
              title=f"{trend_feeder} -- forecast vs delivered energy", color_discrete_sequence=TCN_COLORS)
fig2.update_layout(**TCN_CHART_LAYOUT)
_style_chart(fig2)
st.plotly_chart(fig2, use_container_width=True)

st.subheader("Forecast vs delivered -- daily detail")
detail = daily[["reading_date", "region", "station", "feeder", "opening_meter_mwh", "closing_meter_mwh",
                "daily_energy_mwh", "actual_sum", "cross_check_mismatch", "forecast_mwh",
                "band_used", "band_source", "rate_used", "rate_source", "has_flag"]].rename(
    columns={"actual_sum": "actual_mwh_crosscheck"}).sort_values(["reading_date", "station", "feeder"])
st.dataframe(one_indexed(detail), use_container_width=True)
st.download_button("Download daily detail (CSV)", detail.to_csv(index=False), "feeder_daily_energy.csv", "text/csv")

if not excluded.empty:
    st.subheader(f"Excluded from naira totals ({len(excluded)}) -- flagged R32/R33/R35")
    st.caption("These feeder-days have at least one reading flagged as a meter reset/rollover (R32), "
               "above-limit energy (R33), or an opening-reading mismatch (R35) -- excluded from value totals above.")
    st.dataframe(one_indexed(excluded[["reading_date", "region", "station", "feeder",
                                       "daily_energy_mwh", "actual_sum"]]), use_container_width=True)
