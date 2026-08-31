"""
### FILE: pages/17_About.py
About page: what the app is, and the team behind it.
"""

import streamlit as st
from utils.auth import login
from utils.branding import inject_css, page_header, credits_section

login()

st.set_page_config(page_title="About", page_icon="⚡", layout="wide")
inject_css()
page_header("About", "33kV Feeder Network · Load & Outage Analytics")

st.markdown(
    """
    **Load & Outage Analytics** is TCN's operations dashboard for the 33kV feeder
    network, built to give regional and head-office teams one place to track load
    and reliability across all 10 regions.

    It brings together hourly feeder, line, and transformer load readings with
    outage records, so anyone can drill from a national overview down to a single
    station or feeder — see peak and average loading, review outage frequency and
    duration by cause or party responsible, and generate reliability (SAIDI/SAIFI-style)
    and regional reports. Data comes in through guided Excel/CSV uploads with
    built-in validation, and every upload, edit, and deletion is logged for audit.
    Access is region-scoped: regional users see and act on their own region only,
    while a Super Admin has full visibility and user management across all regions.

    For 330kV/132kV equipment outages, see the companion **330kV · 132kV Outage Manager**
    app (linked in the sidebar).
    """
)

credits_section()
