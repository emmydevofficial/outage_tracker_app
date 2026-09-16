"""Pull Data -- choose a region, a report type, and a sheet tab, pull that
section's rows into the database on demand. Rendered by app.py's router
(login/CSS done)."""
import streamlit as st

import db
from auth import is_admin, current_region
from activity_log import log_activity
from branding import page_header, one_indexed
from sheet_sync import (
    list_sheet_tabs,
    pull_and_save_outage_requests,
    pull_and_save_incident_reports,
    pull_and_save_daily_max_min,
)

ALL_REGIONS = [
    "ABUJA", "BAUCHI", "BENIN", "ENUGU", "KADUNA", "KANO",
    "LAGOS", "OSOGBO", "PORTHARCOURT", "SHIRORO",
]
REPORT_TYPES = {
    "Outage Request": {"sync_key": "outage_request", "pull_fn": pull_and_save_outage_requests, "log_action": "pull_outage_requests"},
    "Incident Report": {"sync_key": "incident_report", "pull_fn": pull_and_save_incident_reports, "log_action": "pull_incident_reports"},
    "Daily Max/Min": {"sync_key": "daily_max_min", "pull_fn": pull_and_save_daily_max_min, "log_action": "pull_daily_max_min"},
}

page_header("Pull Data", "Sync a region's sheet into the database")

col1, col2 = st.columns(2)
with col1:
    if is_admin():
        selected_region = st.selectbox("Region", options=ALL_REGIONS, key="pull_region")
    else:
        selected_region = current_region()
        st.text_input("Region", value=selected_region or "—", disabled=True)
with col2:
    report_type_label = st.selectbox(
        "Report Type", options=list(REPORT_TYPES.keys()), key="pull_report_type",
    )
report_type = REPORT_TYPES[report_type_label]

if report_type is None:
    st.info("This report type isn't wired up yet — check back in a later phase.")
    st.stop()

if not selected_region:
    st.warning("You don't have a region assigned. Ask an admin to set one on your account.")
    st.stop()

configured_url = db.get_web_app_url(selected_region)
if not configured_url:
    st.warning(
        f"**{selected_region}** has no Apps Script Web App URL configured yet. "
        "Set it on the **Web App Settings** page first."
    )
    st.stop()

st.divider()

tabs_result = list_sheet_tabs(selected_region)
if not tabs_result.get("ok"):
    st.error(f"Couldn't reach {selected_region}'s sheet: {tabs_result.get('error')}")
    st.stop()

available_tabs = tabs_result.get("tabs", [])
if not available_tabs:
    st.warning("The sheet returned no tabs.")
    st.stop()

c1, c2 = st.columns([2, 1])
with c1:
    selected_tab = st.selectbox("Sheet tab to pull", options=available_tabs, key="pull_tab")
with c2:
    st.markdown("&nbsp;")
    pull_clicked = st.button("🔄 Pull Latest", use_container_width=True, type="primary")

if pull_clicked:
    with st.spinner(f"Pulling '{selected_tab}' from {selected_region}..."):
        result = report_type["pull_fn"](selected_region, selected_tab)
    if result.get("ok"):
        st.success(
            f"Synced **{selected_region} / {selected_tab}** ({report_type_label}): "
            f"{result.get('inserted', 0)} new, {result.get('updated', 0)} updated, "
            f"{len(result.get('rejected', []))} rejected."
        )
        if result.get("rejected"):
            with st.expander(f"{len(result['rejected'])} rejected row(s)"):
                st.dataframe(result["rejected"], use_container_width=True)
        log_activity(
            report_type["log_action"],
            f"{selected_region}/{selected_tab}: "
            f"{result.get('inserted', 0)} new, {result.get('updated', 0)} updated, "
            f"{len(result.get('rejected', []))} rejected",
        )
    else:
        st.error(f"Pull failed: {result.get('error')}")

st.divider()
st.subheader("Recent syncs")
runs_df = db.read_sync_runs(region=None if is_admin() else selected_region, limit=25)
if runs_df.empty:
    st.caption("No syncs recorded yet.")
else:
    show = runs_df[[
        "region", "report_type", "source_tab_name", "started_at", "completed_at",
        "rows_received", "rows_inserted", "rows_updated", "rows_rejected", "status",
    ]].rename(columns={
        "region": "Region", "report_type": "Type", "source_tab_name": "Tab",
        "started_at": "Started", "completed_at": "Completed", "rows_received": "Received",
        "rows_inserted": "Inserted", "rows_updated": "Updated", "rows_rejected": "Rejected",
        "status": "Status",
    })
    st.dataframe(one_indexed(show), use_container_width=True, height=360)
