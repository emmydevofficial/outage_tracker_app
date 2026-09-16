"""Incident & Outage Request app -- standalone Streamlit app (router).

Own database (DATABASE_INCIDENT_URL), own users table, own login screen,
cross-linked to the 33kV Load & Outage Analytics app and the TCN 330kV/
132kV Outage Manager app.

Navigation is built explicitly with st.navigation()/st.Page() -- the menu
sits in the sidebar. Admin-only pages are simply left out of the menu for
operators (not shown as "access denied"). The page scripts live in
views/, NOT pages/, so there's no second auto-generated menu.

Outage Request, Incident Report, and Daily Max/Min are all wired up, each
with its own table and its own pair of views.
"""
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Incident & Outage Request", page_icon="📋", layout="wide")

from auth import login, is_admin
from branding import inject_css

login()
inject_css()

_menu = [
    st.Page("views/home.py", title="Home", icon=":material/home:", default=True),
    st.Page("views/pull_data.py", title="Pull Data", icon=":material/sync:"),
]
_outage_request = [
    st.Page("views/outage_requests.py", title="Outage Requests", icon=":material/list_alt:"),
    st.Page("views/analysis.py", title="Outage Request Analysis", icon=":material/analytics:"),
]
_incident_report = [
    st.Page("views/incident_reports.py", title="Incident Reports", icon=":material/report:"),
    st.Page("views/incident_analysis.py", title="Incident Report Analysis", icon=":material/analytics:"),
]
_daily_max_min = [
    st.Page("views/daily_max_min.py", title="Daily Max/Min", icon=":material/monitoring:"),
    st.Page("views/daily_max_min_analysis.py", title="Daily Max/Min Analysis", icon=":material/analytics:"),
]
_admin = [
    st.Page("views/web_app_settings.py", title="Web App Settings", icon=":material/settings:"),
    st.Page("views/users.py", title="Users", icon=":material/group:"),
    st.Page("views/activity_log.py", title="Activity Log", icon=":material/history:"),
]

nav = {
    "Menu": _menu,
    "Outage Request": _outage_request,
    "Incident Report": _incident_report,
    "Daily Max/Min": _daily_max_min,
}
if is_admin():
    nav["Admin"] = _admin

st.navigation(nav).run()
