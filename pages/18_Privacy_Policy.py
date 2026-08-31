"""
### FILE: pages/18_Privacy_Policy.py
Privacy policy: what data this internal tool collects, how it's used,
retained, and protected.
"""

import streamlit as st
from utils.auth import login
from utils.branding import inject_css, page_header

login()

st.set_page_config(page_title="Privacy Policy", page_icon="⚡", layout="wide")
inject_css()
page_header("Privacy Policy", "33kV Feeder Network · Load & Outage Analytics")

st.caption("Last updated: August 2026")

st.markdown(
    """
    **Load & Outage Analytics** is an internal operations tool built for the
    Transmission Company of Nigeria (TCN). It is not a public-facing service —
    access is restricted to authorized TCN personnel, and this policy describes
    how their data is handled within the app.
    """
)

st.subheader("Information We Collect")
st.markdown(
    """
    - **Account information** — username, a securely hashed password (never
      stored or displayed in plain text), assigned role (Regional User or
      Super Admin), and assigned region.
    - **Operational data** — hourly feeder, line, and transformer load
      readings, and outage records, uploaded by authorized users via the
      app's upload pages. This is grid operations data, not personal or
      customer data.
    - **Uploaded files** — the original Excel/CSV files submitted through the
      upload pages are kept on the server as a record of what was imported.
    - **Activity logs** — a record of state-changing actions (logins, uploads,
      edits, deletions, user management) including the username, role,
      region, and timestamp. Read-only page views are not logged.
    """
)

st.subheader("How We Use This Information")
st.markdown(
    """
    - To power the dashboards, analytics, and reports throughout the app.
    - To enforce region-scoped access, so regional users only see and act on
      their own region's data.
    - To maintain an audit trail for accountability — the Activity Log page
      (Super Admin only) shows who did what, and when.
    """
)

st.subheader("Data Retention")
st.markdown(
    """
    - **Uploaded files** are kept for **90 days**, after which the file is
      deleted from the server automatically; its entry stays in the
      Uploaded Files directory for historical record, marked as expired.
    - **Load, outage, and activity records** are retained indefinitely as
      part of TCN's operational history, unless explicitly deleted through
      the app's data management pages by an authorized user.
    - **Account credentials** are retained for as long as the account exists,
      and removed when a Super Admin deletes the account.
    """
)

st.subheader("Access Control & Security")
st.markdown(
    """
    - Passwords are hashed with **bcrypt** before storage; the app never
      stores or transmits plain-text passwords.
    - Access is **region-scoped by default** — a regional user cannot see or
      modify another region's data, and rows with missing region
      information are hidden rather than shown by default.
    - A **Super Admin** has full visibility across all regions and manages
      user accounts.
    - The database connection and its credentials are kept server-side and
      are never exposed to the browser.
    """
)

st.subheader("Data Sharing")
st.markdown(
    """
    This data is not sold, shared, or made available to any third party. It
    stays within TCN's own infrastructure — the application server and its
    database.
    """
)

st.subheader("Your Responsibilities")
st.markdown(
    """
    - Keep your login credentials confidential and do not share them.
    - Log out when using a shared or public device.
    - Only upload operational data you are authorized to submit.
    """
)

st.subheader("Questions")
st.markdown(
    "For questions about this policy or your data, contact your Super Admin "
    "or TCN Operations' Department TCC. See the **About** page (in the sidebar) for the project team."
)
