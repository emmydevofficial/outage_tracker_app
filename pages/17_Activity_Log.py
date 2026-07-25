"""
### FILE: pages/17_Activity_Log.py
Super Admin only: audit trail of state-changing actions (logins, uploads,
deletes, user management) plus a directory of every uploaded file.

Uploaded files are kept for 90 days; the purge is lazy (runs whenever this
page loads) since a Streamlit app has no persistent background scheduler --
see utils/file_storage.py and purge_expired_uploads.py for a standalone
script that can optionally be wired into a real OS cron for exact-day timing.
"""

import streamlit as st
from utils.auth import login, require_super_admin

login()
require_super_admin()

from datetime import date, timedelta

from utils.activity_log import read_activity_log, list_activity_actions
from utils.file_storage import list_uploaded_files, purge_expired_files, get_file_bytes
from utils.branding import inject_css, page_header

st.set_page_config(page_title="Activity Log", page_icon="⚡", layout="wide")
inject_css()
page_header("Activity Log & Uploaded Files", "33kV Feeder Network · Audit Trail")
st.caption("Super Admin only. Read-only page views are not logged -- only actions that change data.")

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Activity Log
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("Activity Log")

f1, f2, f3 = st.columns([1.5, 1.5, 2])
with f1:
    action_options = ["All"] + list_activity_actions()
    action_choice = f1.selectbox("Action", action_options)
with f2:
    username_choice = f2.text_input("Username contains (optional)")
with f3:
    today = date.today()
    start_default = today - timedelta(days=30)
    log_start, log_end = f3.date_input("Date range", value=[start_default, today], key="log_dates")

log_df = read_activity_log(
    limit=500,
    action=None if action_choice == "All" else action_choice,
    start_date=log_start,
    end_date=log_end + timedelta(days=1),
)
if username_choice:
    log_df = log_df[log_df["username"].astype(str).str.contains(username_choice, case=False, na=False)]

st.dataframe(log_df, use_container_width=True, height=400)
st.caption(f"Showing {len(log_df):,} of the most recent 500 matching entries.")

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Uploaded Files Directory
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("📁 Uploaded Files Directory")
st.markdown(
    "Every file uploaded through the app, kept for **90 days** before the underlying "
    "file is deleted from disk (its record stays here for history). "
    "TCN Outage Manager files are exempt, since that page reads them as live data."
)

purged_now = purge_expired_files()
if purged_now:
    st.info(f"🧹 Purged {purged_now} expired file(s) on this page load.")

if st.button("Purge Expired Files Now"):
    purged = purge_expired_files()
    if purged:
        st.success(f"Purged {purged} expired file(s).")
    else:
        st.info("No expired files to purge right now.")
    st.rerun()

files_df = list_uploaded_files()
if files_df.empty:
    st.info("No files have been uploaded yet.")
else:
    for _, row in files_df.iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([2.5, 1.3, 1, 1.5, 1.3, 1.3])
        c1.markdown(f"**{row['original_filename']}**  \n`{row['source_page']}`")
        c2.text(row["region"] or "— all —")
        c2.caption(row["username"] or "")
        size_kb = (row["file_size_bytes"] or 0) / 1024
        c3.text(f"{size_kb:,.1f} KB")
        c4.text(f"Uploaded: {row['uploaded_at']:%Y-%m-%d %H:%M}")
        if row["deleted_at"] is not None:
            c5.markdown("🗑️ Expired/Deleted")
        else:
            c5.text(f"Expires: {row['expires_at']:%Y-%m-%d}")
        with c6:
            if row["deleted_at"] is None:
                data = get_file_bytes(row["stored_path"])
                if data is not None:
                    st.download_button(
                        "Download", data, file_name=row["original_filename"],
                        key=f"dl_{row['id']}", use_container_width=True,
                    )
                else:
                    st.caption("File missing")
        st.markdown("---")

    st.caption(f"{len(files_df):,} file(s) total.")
