"""
### FILE: views/home.py
Home/landing page. Rendered by app.py's st.navigation router, which has
already run login() + inject_css().
"""
import streamlit as st
from utils.branding import page_header, credits_section

page_header("Load & Outage Analytics", "33kV Feeder Network · Load · Outages · Analytics")

st.markdown(
    """
    This app tracks 33kV feeder load and outages across all 10 regions. Use the
    sidebar menu, grouped by what you're doing:

    - **Upload Data** — feeder/line/transformer load readings, outage records
    - **Load Analysis** — region, station, feeder, and transformer load drilldowns
    - **Outage & Reliability** — outage analytics, SAIDI/SAIFI-style KPIs, regional comparison
    - **Reports** — generate outage reports by region, daily narrative outage report
    - **Data Management** — correct or remove previously uploaded records
    - **Admin** — user management, activity log, tariff settings (Super Admin only)
    - **Info** — about this app, privacy policy

    Access is region-scoped: regional users see and act on their own region only,
    while a Super Admin has full visibility across all regions.
    """
)

st.sidebar.header("Quick actions")
if st.sidebar.button("Refresh data cache"):
    st.rerun()

# credits_section()
