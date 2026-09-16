"""Activity Log -- audit trail of state-changing actions. Admin-only
(defence-in-depth; the router leaves it out of the menu for operators).
Rendered by app.py's router."""
from datetime import date, timedelta

import streamlit as st

from auth import require_admin
from activity_log import read_activity_log, list_activity_actions
from branding import page_header, one_indexed

page_header("Activity Log", "Audit trail")
require_admin()
st.caption("Read-only page views are not logged — only actions that change data or run a sync.")

f1, f2, f3 = st.columns([1.5, 1.5, 2])
with f1:
    action_choice = st.selectbox("Action", ["All"] + list_activity_actions())
with f2:
    username_choice = st.text_input("Username contains (optional)")
with f3:
    today = date.today()
    log_start, log_end = st.date_input(
        "Date range", value=(today - timedelta(days=30), today), key="log_dates"
    )

log_df = read_activity_log(
    limit=500,
    action=None if action_choice == "All" else action_choice,
    start_date=log_start,
    end_date=log_end + timedelta(days=1),
)
if username_choice:
    log_df = log_df[log_df["username"].astype(str).str.contains(username_choice, case=False, na=False)]

st.dataframe(one_indexed(log_df), use_container_width=True, height=460)
st.caption(f"Showing {len(log_df):,} of the most recent 500 matching entries.")
