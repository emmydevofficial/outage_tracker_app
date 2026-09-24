"""
### FILE: app.py
Entry point / router. Builds the sidebar menu explicitly with
st.navigation()/st.Page(), grouped into sections instead of one long flat
list -- Streamlit's old pages/-folder auto-nav has no grouping support at
all, which is why it grew into a 20-item list. Admin-only pages are simply
left out of the menu for regional users (not shown as "access denied").
Page scripts live in views/, NOT pages/, so there's no second
auto-generated menu competing with this one.

Run with: streamlit run app.py
"""
import streamlit as st
from utils.auth import login, is_super_admin
from utils.branding import inject_css

st.set_page_config(page_title="Power Ops Dashboard", page_icon="⚡", layout="wide")

login()
inject_css()

_home = [
    st.Page("views/home.py", title="Home", icon=":material/home:", default=True),
]
_upload = [
    st.Page("views/upload_outages.py", title="Upload Outages", icon=":material/upload_file:"),
    st.Page("views/upload_feeder_load.py", title="Upload Feeder Load", icon=":material/upload_file:"),
    st.Page("views/upload_line_load.py", title="Upload Line Load", icon=":material/upload_file:"),
    st.Page("views/upload_transformer_load.py", title="Upload Transformer Load", icon=":material/upload_file:"),
]
_load_analysis = [
    st.Page("views/region_load_analysis.py", title="Region Load Analysis", icon=":material/bar_chart:"),
    st.Page("views/station_load_analysis.py", title="Station Load Analysis", icon=":material/bar_chart:"),
    st.Page("views/feeder_load_analysis.py", title="Feeder Load Analysis", icon=":material/bar_chart:"),
    st.Page("views/transformer_load.py", title="Transformer Load", icon=":material/bar_chart:"),
]
_outage_reliability = [
    st.Page("views/outage_analytics.py", title="Outage Analytics", icon=":material/bolt:"),
    st.Page("views/reliability_kpi_report.py", title="Reliability KPI Report", icon=":material/monitoring:"),
    st.Page("views/regional_dashboard.py", title="Regional Dashboard", icon=":material/dashboard:"),
]
_reports = [
    st.Page("views/generate_reports.py", title="Generate Reports", icon=":material/description:"),
    st.Page("views/daily_outage_report.py", title="Daily Outage Report", icon=":material/newspaper:"),
]
_data_management = [
    st.Page("views/outage_management.py", title="Outage Management", icon=":material/edit_note:"),
    st.Page("views/load_data_management.py", title="Load Data Management", icon=":material/edit_note:"),
]
_admin = [
    st.Page("views/user_management.py", title="User Management", icon=":material/group:"),
    st.Page("views/activity_log.py", title="Activity Log", icon=":material/history:"),
    st.Page("views/tariff_settings.py", title="Tariff Settings", icon=":material/payments:"),
]
_info = [
    st.Page("views/about.py", title="About", icon=":material/info:"),
    st.Page("views/privacy_policy.py", title="Privacy Policy", icon=":material/shield:"),
]

nav = {
    "Menu": _home,
    "Upload Data": _upload,
    "Load Analysis": _load_analysis,
    "Outage & Reliability": _outage_reliability,
    "Reports": _reports,
    "Data Management": _data_management,
    "Info": _info,
}
if is_super_admin():
    nav["Admin"] = _admin

st.navigation(nav).run()
