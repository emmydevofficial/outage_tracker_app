"""Login/session gate for the Incident & Outage Request app.

Copies the 33kV app's utils/auth.py login flow (same render_login_screen()
call, same cookie-first-then-form logic) but against this app's own users
table/roles ('admin' / 'operator', matching TCN-Outage-Manager-main's
scheme rather than the 33kV app's 'super_admin' / 'regional_user') and its
own session cookie module.
"""
import os

import pandas as pd
import streamlit as st
from sqlalchemy import text
import bcrypt

from db import get_engine, get_user_role_and_region
from activity_log import log_activity
from branding import render_login_screen
from session_cookie import issue_session_cookie, read_session_username, clear_session_cookie

LOAD_OUTAGE_ANALYTICS_URL = os.getenv("LOAD_OUTAGE_ANALYTICS_URL", "http://93.127.137.148:8501")
TCN_OUTAGE_MANAGER_URL = os.getenv("TCN_OUTAGE_MANAGER_URL", "http://93.127.137.148:8502")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def authenticate(username: str, password: str) -> bool:
    if not username or not password:
        return False
    engine = get_engine()
    query = text("SELECT password_hash FROM users WHERE username = :u")
    try:
        with engine.connect() as conn:
            row = conn.execute(query, {"u": username}).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    return _verify_password(password, row[0])


def login():
    """Render a full-page login screen and enforce authentication.

    Call this at the top of the app. Shows the login form if not already
    authenticated; on success, reruns and lets the rest of the app render.
    """
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = None
        st.session_state.role = None
        st.session_state.region = None

    if not st.session_state.logged_in:
        # Fresh tab/session -- check for a valid session cookie before
        # showing the login form (shared across every tab of the same
        # browser). Role/region are re-fetched fresh from the database
        # rather than trusted from the cookie, so a demoted/deleted
        # account takes effect immediately.
        cookie_username = read_session_username()
        if cookie_username:
            role, region = get_user_role_and_region(cookie_username)
            if role:
                st.session_state.logged_in = True
                st.session_state.username = cookie_username
                st.session_state.role = role
                st.session_state.region = region

    if st.session_state.logged_in:
        region_label = st.session_state.get("region") or "All Regions"
        role_label = "Admin" if st.session_state.get("role") == "admin" else "Operator"
        st.sidebar.caption(f"👤 {st.session_state.get('username', '')} — {role_label} — {region_label}")
        st.sidebar.link_button(
            "📊 Load & Outage Analytics (33kV)", LOAD_OUTAGE_ANALYTICS_URL, use_container_width=True,
        )
        st.sidebar.link_button(
            "🗼 330kV · 132kV Outage Manager", TCN_OUTAGE_MANAGER_URL, use_container_width=True,
        )
        if st.sidebar.button("Logout"):
            log_activity("logout")
            clear_session_cookie()
            st.session_state.logged_in = False
            st.session_state.username = None
            st.session_state.role = None
            st.session_state.region = None
            st.rerun()
        else:
            # sliding expiry: every active render while logged in resets
            # the cookie's ~1 hour window, so an actively-used session
            # never times out; only real inactivity does.
            issue_session_cookie(st.session_state.username)
        return

    def _verify(u, p):
        if not authenticate(u, p):
            return False, None, None
        role, region = get_user_role_and_region(u)
        return True, role, region

    username, password, submitted, result = render_login_screen(
        verify_fn=_verify, subtitle="Incident & Outage Request"
    )

    if submitted:
        ok, role, region = result
        if ok:
            st.session_state.logged_in = True
            st.session_state.username = username
            st.session_state.role = role
            st.session_state.region = region
            issue_session_cookie(username)
            log_activity("login")
            st.rerun()
        else:
            log_activity("login_failed", f"attempted username: '{username}'")
            st.session_state["_login_error"] = "Invalid username or password."
            st.rerun()
    st.stop()


# -----------------------------
# ACCESS CONTROL HELPERS
# -----------------------------

def is_admin() -> bool:
    return st.session_state.get("role") == "admin"


def current_region() -> str | None:
    """The logged-in user's assigned region, or None for an admin."""
    return st.session_state.get("region")


def require_admin():
    """Stop the page with an access-denied message unless the user is an admin."""
    if not is_admin():
        st.error("⛔ Access denied — this page is restricted to Admins.")
        st.stop()


def filter_to_user_region(df: pd.DataFrame, region_col: str = "region") -> pd.DataFrame:
    """Filter a dataframe to the current user's region; no-op for admins.

    Comparison is case-insensitive; regional operators never see rows with
    a null/blank region -- deny by default.
    """
    if is_admin():
        return df
    region = current_region()
    if not region or region_col not in df.columns:
        return df.iloc[0:0]
    mask = df[region_col].astype(str).str.strip().str.upper() == region.strip().upper()
    return df[mask]
