"""
### FILE: pages/19_Tariff_Settings.py
Super Admin only: set the Naira/kWh tariff used to estimate the cost of
TCN's outage-hour exceedance on the Reliability KPI Report page.

Two levels: a single global default rate, and per-(disco, feeder band)
overrides. A feeder with no band assigned is treated as Band A; a feeder
with no disco, or a (disco, band) combo with no override saved yet, falls
back to the global default. See utils/tariff.py for the lookup logic.
"""

import streamlit as st
from utils.auth import login, require_super_admin

login()
require_super_admin()

import pandas as pd
from utils.db import (
    read_tariff_settings,
    read_tariff_rates,
    list_known_discos,
    update_tariff_settings,
    upsert_tariff_rate,
)
from utils.activity_log import log_activity
from utils.branding import inject_css, page_header

st.set_page_config(page_title="Tariff Settings", page_icon="⚡", layout="wide")
inject_css()
page_header("Tariff Settings", "33kV Feeder Network · Outage Exceedance Cost")
st.caption(
    "Super Admin only. These rates drive the estimated cost figures on the "
    "Reliability KPI Report page's TCN Outage Hours Exceedance table."
)

BANDS = ["A", "B", "C", "D"]

# ══════════════════════════════════════════════════════════════════════════
# Default tariff rate
# ══════════════════════════════════════════════════════════════════════════
st.subheader("Default Tariff Rate")
st.caption(
    "Used whenever a feeder has no disco, or its (disco, band) has no rate "
    "set below yet."
)

current_default = read_tariff_settings()
with st.form("default_rate_form"):
    new_default = st.number_input(
        "Default rate (₦/kWh)", min_value=0.0, value=float(current_default),
        step=0.5, format="%.2f",
    )
    if st.form_submit_button("Save Default Rate", type="primary"):
        update_tariff_settings(new_default, st.session_state.get("username"))
        read_tariff_settings.clear()
        log_activity("update_tariff_default", f"Default tariff rate set to ₦{new_default:,.2f}/kWh")
        st.success(f"Default rate updated to ₦{new_default:,.2f}/kWh.")
        st.rerun()

st.divider()

# ══════════════════════════════════════════════════════════════════════════
# Per-disco / band rates
# ══════════════════════════════════════════════════════════════════════════
st.subheader("Per-Disco Band Rates")
st.caption(
    "One rate per disco + band. Any combination left unset uses the default "
    "rate above until saved here."
)

discos = list_known_discos()
existing_rates = read_tariff_rates()

grid_rows = []
for disco in discos:
    for band in BANDS:
        match = existing_rates[
            (existing_rates["disco"] == disco) & (existing_rates["band"] == band)
        ] if not existing_rates.empty else pd.DataFrame()
        rate = float(match.iloc[0]["rate_ngn_per_kwh"]) if not match.empty else float(current_default)
        grid_rows.append({"Disco": disco, "Band": band, "Rate (₦/kWh)": rate})

grid_df = pd.DataFrame(grid_rows)

edited_df = st.data_editor(
    grid_df,
    use_container_width=True,
    hide_index=True,
    disabled=["Disco", "Band"],
    column_config={
        "Rate (₦/kWh)": st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.2f"),
    },
    key="tariff_rate_editor",
)

if st.button("Save Changes", type="primary"):
    for _, row in edited_df.iterrows():
        upsert_tariff_rate(row["Disco"], row["Band"], float(row["Rate (₦/kWh)"]), st.session_state.get("username"))
    read_tariff_rates.clear()
    log_activity("update_tariff_rates", f"Updated {len(edited_df)} disco/band rate(s)")
    st.success(f"Saved {len(edited_df)} disco/band rate(s).")
    st.rerun()
