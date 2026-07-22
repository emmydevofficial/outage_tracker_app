"""
### FILE: pages/outage_management.py

Outage management page — provides two privileged delete operations:

  1. Delete by date   — available to any authenticated user, scoped to their own
                        region for regional users (all regions for Super Admin).
                        Requires the acting user's own valid password.
  2. Truncate table   — Super Admin only; the section isn't even shown to
                        regional users, and the acting user's role is re-checked
                        against the DB before the truncate runs.

The page also re-uses the existing session_state login stored by the app so
that the logged-in username is pre-filled.  If no login state is present the
user is asked to identify themselves inline.
"""

import streamlit as st
from utils.auth import login, is_super_admin, current_region
from utils.activity_log import log_activity
from datetime import date, timedelta

from utils.db import (
    verify_user_password,
    get_user_role,
    delete_outages_by_date,
    truncate_outages,
    read_outages,
)

login()
# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Outage Management", layout="wide")
st.title("⚡ Outage Record Management")
st.caption(
    "Use the sections below to delete outage records. "
    "All operations are permanent and cannot be undone."
)

# ── helper: resolve the current username from session_state ───────────────────
def _current_username() -> str:
    """Return the username stored in session_state (if any)."""
    return st.session_state.get("username", "")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Delete outages by date_off
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("🗑️ Delete Outages by Date")
if is_super_admin():
    st.markdown(
        "Removes **all** outage rows whose `date_off` matches the chosen date, "
        "across every region. Your password is required to confirm the operation."
    )
else:
    st.markdown(
        f"Removes outage rows whose `date_off` matches the chosen date, **for {current_region()} only**. "
        "Your password is required to confirm the operation."
    )

with st.form("form_delete_by_date", border=True):
    col_date, col_user, col_pwd = st.columns([2, 2, 2])

    with col_date:
        today = date.today()
        start_default = today - timedelta(days=30)        
        start_date, end_date = st.date_input(
            "Date off (date_off)",
            value=[start_default, today],
            min_value=date(2000, 1, 1),
            max_value=date.today(),
            key="del_date_input",
        )

    with col_user:
        del_username = st.text_input(
            "Your username",
            value=_current_username(),
            key="del_username",
        )

    with col_pwd:
        del_password = st.text_input(
            "Your password",
            type="password",
            key="del_password",
        )

    # preview row count before committing
    preview_col, submit_col = st.columns([3, 1])
    with submit_col:
        submitted_delete = st.form_submit_button(
            "🗑️ Delete Records",
            type="primary",
            use_container_width=True,
        )

if submitted_delete:
    # --- validation ---
    if not del_username or not del_password:
        st.error("Username and password are required.")
    elif not verify_user_password(del_username, del_password):
        st.error("❌ Invalid credentials. Operation aborted.")
    else:
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")
        try:
            scope_region = None if is_super_admin() else current_region()
            rows_deleted = delete_outages_by_date(start_date, end_date, region=scope_region)
            if rows_deleted == 0:
                st.warning(
                    f"No outage records found for **{start_date} to {end_date}**"
                    + (f" in {scope_region}" if scope_region else "") + ". Nothing was deleted."
                )
            else:
                st.success(
                    f"✅ Successfully deleted **{rows_deleted}** outage record(s) "
                    f"for date **{start_date} to {end_date}**"
                    + (f" in {scope_region}" if scope_region else "") + "."
                )
                log_activity(
                    "delete_outages",
                    f"Deleted {rows_deleted} row(s) for {start_date} to {end_date}"
                    + (f" ({scope_region})" if scope_region else " (all regions)"),
                )
                # clear cached query results so downstream pages refresh
                read_outages.clear()
        except Exception as exc:
            st.error(f"Database error: {exc}")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Truncate entire outages table (Super Admin only)
# ══════════════════════════════════════════════════════════════════════════════
if is_super_admin():
    st.subheader("💣 Truncate All Outage Records")
    st.markdown(
        "> ⚠️ **Danger zone.** This will permanently erase **every row** in the "
        "`outages` table (every region) and reset the auto-increment ID to 1. "
        "**This action cannot be undone.**  "
        "Only **Super Admins** may perform this operation."
    )

    with st.form("form_truncate_outages", border=True):
        trunc_col_user, trunc_col_pwd = st.columns([2, 2])

        with trunc_col_user:
            trunc_username = st.text_input(
                "Admin username",
                value=_current_username(),
                key="trunc_username",
            )

        with trunc_col_pwd:
            trunc_password = st.text_input(
                "Admin password",
                type="password",
                key="trunc_password",
            )

        confirm_check = st.checkbox(
            "I understand this will delete ALL outage records and cannot be reversed.",
            key="trunc_confirm_check",
        )

        submitted_truncate = st.form_submit_button(
            "💣 Truncate Outages Table",
            type="primary",
            use_container_width=False,
        )

    if submitted_truncate:
        # --- validation ---
        if not trunc_username or not trunc_password:
            st.error("Username and password are required.")
        elif not confirm_check:
            st.error("You must tick the confirmation checkbox before proceeding.")
        elif not verify_user_password(trunc_username, trunc_password):
            st.error("❌ Invalid credentials. Operation aborted.")
        else:
            role = get_user_role(trunc_username)
            if role != "super_admin":
                st.error(
                    f"❌ Access denied. The user **{trunc_username}** has role "
                    f"**{role or 'unknown'}**. Only **Super Admin** users can truncate the table."
                )
            else:
                try:
                    truncate_outages()
                    st.success(
                        "✅ The outages table has been truncated and the ID sequence reset."
                    )
                    log_activity("truncate_outages", "Truncated the entire outages table")
                    read_outages.clear()
                except Exception as exc:
                    st.error(f"Database error: {exc}")