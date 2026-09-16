"""Home / landing view. Rendered by app.py's st.navigation router, which
has already run login() + inject_css()."""
import streamlit as st

import db
from auth import is_admin
from branding import page_header, kpi_card, kpi_grid, TCN_BLUE, TCN_RED

page_header("Incident & Outage Request", "330kV/132kV Regional Control Centres · Sheet Sync")

st.markdown(
    "Pulls outage-request, incident and daily max/min data out of each region's "
    "Google Sheet and into one place. Use the menu on the left."
)

col_a, col_b = st.columns(2)
with col_a:
    st.markdown(
        "#### 🔄 Pull Data\n"
        "Choose a region, a report type (Outage Request, Incident Report, or "
        "Daily Max/Min), and a sheet tab, then pull. Every pull is additive and "
        "permanent — new rows are added, changed rows are updated, and nothing "
        "already saved is ever removed or hidden."
    )
    st.markdown(
        "#### 📋 Records pages\n"
        "The synced records as a table for each report type — filter by date "
        "and region; Outage Requests/Incident Reports also filter by which "
        "date field (application, scheduled, incident, closure)."
    )
with col_b:
    st.markdown(
        "#### 📈 Analysis pages\n"
        "One analysis page per report type: station/DISCO/type/category "
        "breakdowns, trends over time, timing metrics (lead time, resolution "
        "time), and load/frequency/voltage trends for Daily Max/Min."
    )
    if is_admin():
        st.markdown(
            "#### ⚙️ Web App Settings\n"
            "Store each of the 10 regions' Apps Script Web App URLs here — the "
            "Pull Data page reads them from this list."
        )

st.divider()

try:
    settings_df = db.list_web_app_settings()
    configured = int(settings_df["web_app_url"].notna().sum())
    recent = db.read_sync_runs(limit=1)
    kpi_grid([
        kpi_card("Regions Configured", f"{configured} / {len(settings_df)}", "", "tower", TCN_BLUE),
        kpi_card(
            "Last Sync",
            recent.iloc[0]["started_at"].strftime("%Y-%m-%d %H:%M") if not recent.empty else "—",
            "", "clock", TCN_RED,
        ),
    ])
except Exception as e:
    st.caption(f"(status unavailable: {e})")
