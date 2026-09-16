"""Daily Max/Min Analysis -- load, frequency, and voltage trends over
time, plus a call-out for under-frequency days. Rendered by app.py's
router (login/CSS done)."""
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import db
from auth import is_admin, current_region
from branding import page_header, one_indexed, kpi_card, kpi_grid, TCN_CHART_LAYOUT, _style_chart, TCN_BLUE, TCN_RED

ALL_REGIONS = [
    "ABUJA", "BAUCHI", "BENIN", "ENUGU", "KADUNA", "KANO",
    "LAGOS", "OSOGBO", "PORTHARCOURT", "SHIRORO",
]
# Standard grid-stability reference band (50 Hz nominal, ±0.5 Hz) -- used
# only to flag notable days below, not asserted as an official TCN limit.
LOW_FREQ_THRESHOLD_HZ = 49.5

page_header("Daily Max/Min Analysis", "Load, frequency & voltage trends")

if is_admin():
    region_choice = st.selectbox("Region", options=["All Regions"] + ALL_REGIONS, key="dma_region")
    selected_region = None if region_choice == "All Regions" else region_choice
else:
    selected_region = current_region()

df = db.read_daily_max_min(region=selected_region)
if df.empty:
    st.info("No daily max/min readings to analyse yet. Pull some on the **Pull Data** page.")
    st.stop()

df["reading_date"] = pd.to_datetime(df["reading_date"], errors="coerce")

st.markdown("#### Filters")
today = date.today()
d1, d2 = st.columns(2)
with d1:
    from_date = st.date_input("From", value=date(today.year, 1, 1), key="dma_from")
with d2:
    to_date = st.date_input("To", value=today, key="dma_to")

if from_date > to_date:
    st.error("'From' is after 'To' — swap them.")
    st.stop()

lo, hi = pd.Timestamp(from_date), pd.Timestamp(to_date)
fdf = df[(df["reading_date"] >= lo) & (df["reading_date"] <= hi)].sort_values("reading_date")

st.divider()
if fdf.empty:
    st.warning("No readings match this date range.")
    st.stop()

under_freq_days = fdf[fdf["min_freq_hz"] < LOW_FREQ_THRESHOLD_HZ]
kpi_grid([
    kpi_card("Days Recorded", f"{len(fdf)}", "", "alert", TCN_BLUE),
    kpi_card("Peak Load", f"{fdf['max_load_mw'].max():.1f}" if fdf['max_load_mw'].notna().any() else "—", "MW", "bolt", TCN_RED),
    kpi_card("Lowest Frequency", f"{fdf['min_freq_hz'].min():.2f}" if fdf['min_freq_hz'].notna().any() else "—", "Hz", "pulse", "#956400"),
    kpi_card("Lowest Voltage", f"{fdf['min_voltage_kv'].min():.0f}" if fdf['min_voltage_kv'].notna().any() else "—", "kV", "tower", TCN_BLUE),
    kpi_card(f"Days Under {LOW_FREQ_THRESHOLD_HZ} Hz", f"{len(under_freq_days)}", "", "alert", TCN_RED),
])

st.markdown("**Load over time** (MW)")
load_long = fdf.melt(
    id_vars=["reading_date"], value_vars=["max_load_mw", "min_load_mw"],
    var_name="series", value_name="mw",
).replace({"series": {"max_load_mw": "Max Load", "min_load_mw": "Min Load"}})
fig = px.line(load_long, x="reading_date", y="mw", color="series", markers=True, color_discrete_sequence=[TCN_RED, TCN_BLUE])
fig.update_layout(**TCN_CHART_LAYOUT, legend_title_text="")
st.plotly_chart(_style_chart(fig), use_container_width=True)

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Frequency over time** (Hz)")
    freq_long = fdf.melt(
        id_vars=["reading_date"], value_vars=["max_freq_hz", "min_freq_hz"],
        var_name="series", value_name="hz",
    ).replace({"series": {"max_freq_hz": "Max Freq", "min_freq_hz": "Min Freq"}})
    fig = px.line(freq_long, x="reading_date", y="hz", color="series", markers=True, color_discrete_sequence=[TCN_RED, TCN_BLUE])
    fig.add_hline(y=LOW_FREQ_THRESHOLD_HZ, line_dash="dot", line_color="#956400",
                   annotation_text=f"{LOW_FREQ_THRESHOLD_HZ} Hz reference")
    fig.update_layout(**TCN_CHART_LAYOUT, legend_title_text="")
    st.plotly_chart(_style_chart(fig), use_container_width=True)
with c2:
    st.markdown("**Voltage over time** (kV)")
    volt_long = fdf.melt(
        id_vars=["reading_date"], value_vars=["max_voltage_kv", "min_voltage_kv"],
        var_name="series", value_name="kv",
    ).replace({"series": {"max_voltage_kv": "Max Voltage", "min_voltage_kv": "Min Voltage"}})
    fig = px.line(volt_long, x="reading_date", y="kv", color="series", markers=True, color_discrete_sequence=[TCN_RED, TCN_BLUE])
    fig.update_layout(**TCN_CHART_LAYOUT, legend_title_text="")
    st.plotly_chart(_style_chart(fig), use_container_width=True)

if not under_freq_days.empty:
    st.markdown(f"**Days under {LOW_FREQ_THRESHOLD_HZ} Hz** (min frequency of the day)")
    show = under_freq_days[["reading_date", "min_freq_hz", "min_freq_time", "max_load_mw"]].rename(columns={
        "reading_date": "Date", "min_freq_hz": "Min Freq (Hz)",
        "min_freq_time": "Time", "max_load_mw": "That Day's Peak Load (MW)",
    })
    show["Date"] = pd.to_datetime(show["Date"], errors="coerce").dt.date
    st.dataframe(one_indexed(show), use_container_width=True)

st.divider()
st.markdown("#### Filtered rows")
show_cols = {
    "reading_date": "Date", "max_load_mw": "Max Load (MW)", "max_load_time": "Max Load Time",
    "max_freq_hz": "Max Freq (Hz)", "max_voltage_kv": "Max Voltage (kV)",
    "min_load_mw": "Min Load (MW)", "min_load_time": "Min Load Time",
    "min_freq_hz": "Min Freq (Hz)", "min_voltage_kv": "Min Voltage (kV)",
}
out = fdf[list(show_cols.keys())].rename(columns=show_cols)
out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.date
st.dataframe(one_indexed(out.sort_values("Date", ascending=False)), use_container_width=True, height=420)
st.caption(f"{len(fdf):,} of {len(df):,} day(s) match the filters.")
