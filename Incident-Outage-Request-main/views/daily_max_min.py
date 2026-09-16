"""Daily Max/Min -- the synced daily load/frequency/voltage readings as a
table with live filtering. Only one date field exists here (reading_date),
so unlike Outage Requests/Incident Reports there's no "filter by date"
picker -- just Region + From/To. Rendered by app.py's router (login/CSS
already done)."""
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

page_header("Daily Max/Min", "Synced daily load, frequency & voltage readings")

c1, c2 = st.columns(2)
with c1:
    if is_admin():
        region_choice = st.selectbox("Region", options=["All Regions"] + ALL_REGIONS, key="dmm_region")
        selected_region = None if region_choice == "All Regions" else region_choice
    else:
        selected_region = current_region()
        st.text_input("Region", value=selected_region or "—", disabled=True)
with c2:
    st.caption("One row per day — Max/Min load, frequency, and voltage for the whole grid that day.")

today = date.today()
d1, d2 = st.columns(2)
with d1:
    from_date = st.date_input("From", value=date(today.year, 1, 1), key="dmm_from")
with d2:
    to_date = st.date_input("To", value=today, key="dmm_to")

st.caption(f"Showing readings whose date falls between {from_date} and {to_date} (inclusive).")

base_df = db.read_daily_max_min(region=selected_region)
total_in_db = len(base_df)

if from_date > to_date:
    st.error("'From' is after 'To' — swap them.")
    st.stop()

if base_df.empty:
    kpi_grid([kpi_card("In Database", "0", "", "building", TCN_BLUE)])
    st.info("No daily max/min readings have been pulled for this region yet. Use the **Pull Data** page.")
    st.stop()

base_df["reading_date"] = pd.to_datetime(base_df["reading_date"], errors="coerce")
lo, hi = pd.Timestamp(from_date), pd.Timestamp(to_date)
fdf = base_df[(base_df["reading_date"] >= lo) & (base_df["reading_date"] <= hi)]

last_run = db.latest_sync_run(selected_region or "ABUJA", "daily_max_min")
if last_run:
    icon = {"success": "✅", "running": "⏳"}.get(last_run["status"], "❌")
    st.caption(f"{icon} Last sync for {selected_region or 'ABUJA'}: {last_run['started_at']}")

kpi_grid([
    kpi_card("In Database", f"{total_in_db}", "days", "building", TCN_BLUE),
    kpi_card("Matching Filters", f"{len(fdf)}", "days", "alert", TCN_RED),
    kpi_card("Peak Load (period)", f"{fdf['max_load_mw'].max():.1f}" if not fdf.empty and fdf['max_load_mw'].notna().any() else "—", "MW", "bolt", TCN_BLUE),
    kpi_card("Lowest Frequency (period)", f"{fdf['min_freq_hz'].min():.2f}" if not fdf.empty and fdf['min_freq_hz'].notna().any() else "—", "Hz", "pulse", "#956400"),
    kpi_card("Lowest Voltage (period)", f"{fdf['min_voltage_kv'].min():.0f}" if not fdf.empty and fdf['min_voltage_kv'].notna().any() else "—", "kV", "tower", TCN_RED),
])

if fdf.empty:
    st.warning(f"No readings between {from_date} and {to_date}.")
    st.stop()

display_cols = {
    "reading_date": "Date",
    "max_load_mw": "Max Load (MW)", "max_load_time": "Max Load Time", "max_load_status": "Max Load Status",
    "max_freq_hz": "Max Freq (Hz)", "max_freq_time": "Max Freq Time",
    "max_voltage_kv": "Max Voltage (kV)", "max_voltage_time": "Max Voltage Time",
    "min_load_mw": "Min Load (MW)", "min_load_time": "Min Load Time", "min_load_status": "Min Load Status",
    "min_freq_hz": "Min Freq (Hz)", "min_freq_time": "Min Freq Time",
    "min_voltage_kv": "Min Voltage (kV)", "min_voltage_time": "Min Voltage Time",
}
out = fdf[list(display_cols.keys())].rename(columns=display_cols)
out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.date
st.dataframe(one_indexed(out), use_container_width=True, height=520)
st.caption(f"Showing {len(fdf):,} of {total_in_db:,} daily reading(s) for this region.")
