"""
### FILE: pages/16_Load_Data_Management.py
Load data management — lets an authenticated user delete hourly load
readings (feeder, line, or transformer) for a date range and region.

  - Regional users: region is fixed to their own assigned region (not
    selectable) -- they can only clear their own region's data.
  - Super Admin: can pick "All Regions" or any single region.
  - Every deletion (any role) requires the acting user's own valid password,
    same pattern as pages/08_Outage_Management.py.

All three load tables (feeder_33kv_load, line_load, transformer_load) share
this same region + reading_date shape, so one page covers all three with a
repeated section rather than three near-identical pages.
"""

import streamlit as st
from utils.auth import login, is_super_admin, current_region
from utils.regions import REGIONS
from datetime import date, timedelta

from utils.db import (
    verify_user_password,
    delete_feeder_load_by_date,
    delete_line_load_by_date,
    delete_transformer_load_by_date,
    read_feeder_load,
    read_line_load,
    read_transformer_load,
)
from utils.activity_log import log_activity

login()

st.set_page_config(page_title="Load Data Management", layout="wide")
st.title("⚡ Load Data Management")
st.caption(
    "Delete hourly load readings by date range and region. "
    "All operations are permanent and cannot be undone."
)


def _current_username() -> str:
    return st.session_state.get("username", "")


def render_delete_section(label: str, table_name: str, delete_fn, cache_clear_fn, key_prefix: str, action_name: str):
    st.subheader(f"🗑️ Delete {label} by Date")
    if is_super_admin():
        st.markdown(
            f"Removes `{table_name}` rows whose `reading_date` falls in the chosen range, "
            "for **All Regions** or a single region you pick below. "
            "Your password is required to confirm the operation."
        )
    else:
        st.markdown(
            f"Removes `{table_name}` rows whose `reading_date` falls in the chosen range, "
            f"**for {current_region()} only**. Your password is required to confirm the operation."
        )

    with st.form(f"form_{key_prefix}", border=True):
        col_date, col_region, col_user, col_pwd = st.columns([2, 1.5, 1.5, 1.5])

        with col_date:
            today = date.today()
            start_default = today - timedelta(days=30)
            start_date, end_date = st.date_input(
                "Reading date range",
                value=[start_default, today],
                min_value=date(2000, 1, 1),
                max_value=date.today(),
                key=f"{key_prefix}_date_input",
            )

        with col_region:
            if is_super_admin():
                region_choice = st.selectbox(
                    "Region", ["All Regions"] + REGIONS, key=f"{key_prefix}_region"
                )
            else:
                st.selectbox(
                    "Region", [current_region()], disabled=True, key=f"{key_prefix}_region"
                )
                region_choice = current_region()

        with col_user:
            del_username = st.text_input(
                "Your username", value=_current_username(), key=f"{key_prefix}_username"
            )

        with col_pwd:
            del_password = st.text_input(
                "Your password", type="password", key=f"{key_prefix}_password"
            )

        _, submit_col = st.columns([3, 1])
        with submit_col:
            submitted = st.form_submit_button(
                f"🗑️ Delete {label} Records", type="primary", use_container_width=True
            )

    if submitted:
        if not del_username or not del_password:
            st.error("Username and password are required.")
        elif not verify_user_password(del_username, del_password):
            st.error("❌ Invalid credentials. Operation aborted.")
        else:
            scope_region = None if (is_super_admin() and region_choice == "All Regions") else region_choice
            try:
                rows_deleted = delete_fn(start_date, end_date, region=scope_region)
                scope_note = f" in {scope_region}" if scope_region else ""
                if rows_deleted == 0:
                    st.warning(f"No {label} records found for **{start_date} to {end_date}**{scope_note}. Nothing was deleted.")
                else:
                    st.success(f"✅ Successfully deleted **{rows_deleted}** {label} record(s) for **{start_date} to {end_date}**{scope_note}.")
                    log_activity(
                        action_name,
                        f"Deleted {rows_deleted} row(s) for {start_date} to {end_date}"
                        + (f" ({scope_region})" if scope_region else " (all regions)"),
                    )
                    cache_clear_fn()
            except Exception as exc:
                st.error(f"Database error: {exc}")

    st.markdown("---")


render_delete_section(
    "Feeder Load", "feeder_33kv_load", delete_feeder_load_by_date, read_feeder_load.clear,
    "feeder_load_del", "delete_feeder_load",
)
render_delete_section(
    "Line Load", "line_load", delete_line_load_by_date, read_line_load.clear,
    "line_load_del", "delete_line_load",
)
render_delete_section(
    "Transformer Load", "transformer_load", delete_transformer_load_by_date, read_transformer_load.clear,
    "transformer_load_del", "delete_transformer_load",
)
