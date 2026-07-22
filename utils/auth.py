import pandas as pd
import streamlit as st
from sqlalchemy import text
import bcrypt

from .db import get_engine, get_user_role_and_region
from .activity_log import log_activity


def hash_password(password: str) -> str:
    # bcrypt operates on bytes, result is bytes; decode to utf-8 for storage
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# kept for any existing internal callers of the old private name
_hash_password = hash_password


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def authenticate(username: str, password: str) -> bool:
    """Verify the supplied credentials against the users table.

    The password stored in the database is a hashed bcrypt string.
    Returns True on success, False otherwise.
    """
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

    stored_hash = row[0]
    return _verify_password(password, stored_hash)


def login():
    """Render a minimal login form in the sidebar and enforce authentication.

    When called at the top of every page, this helper will display a
    username/password form if the user is not already logged in.  On
    successful authentication the page is rerun and further content is
    shown.  If the user fails or has not yet submitted credentials the
    execution is stopped so that the rest of the app doesn't render.
    """
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = None
        st.session_state.role = None
        st.session_state.region = None

    if st.session_state.logged_in:
        region_label = st.session_state.get("region") or "All Regions"
        role_label = "Super Admin" if st.session_state.get("role") == "super_admin" else "Regional User"
        st.sidebar.caption(f"👤 {st.session_state.get('username', '')} — {role_label} — {region_label}")
        # optionally provide a logout button
        if st.sidebar.button("Logout"):
            log_activity("logout")
            st.session_state.logged_in = False
            st.session_state.username = None
            st.session_state.role = None
            st.session_state.region = None
            # newer Streamlit versions use rerun()
            try:
                st.rerun()
            except Exception:
                pass
        return

    st.sidebar.title("🔐 Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")

    if st.sidebar.button("Login"):
        if authenticate(username, password):
            role, region = get_user_role_and_region(username)
            st.session_state.logged_in = True
            st.session_state.username = username
            st.session_state.role = role
            st.session_state.region = region
            log_activity("login")
            try:
                st.rerun()
            except Exception:
                    pass
        else:
            log_activity("login_failed", f"attempted username: '{username}'")
            st.sidebar.error("Invalid username or password.")
    st.stop()


# -----------------------------
# ACCESS CONTROL HELPERS
# -----------------------------

def is_super_admin() -> bool:
    return st.session_state.get("role") == "super_admin"


def current_region() -> str | None:
    """The logged-in user's assigned region, or None for a super_admin."""
    return st.session_state.get("region")


def require_super_admin():
    """Stop the page with an access-denied message unless the user is a super_admin."""
    if not is_super_admin():
        st.error("⛔ Access denied — this page is restricted to Super Admins.")
        st.stop()


def scoped_regions(all_regions) -> list:
    """Region options to offer in a selectbox/multiselect for the current user.

    Super admins get the full list passed in. A regional user only ever gets
    their own region (intersected with what's actually available), so they
    can't pick another region even if the dropdown code doesn't otherwise
    know about access control.
    """
    all_regions = list(all_regions)
    if is_super_admin():
        return all_regions
    region = current_region()
    return [r for r in all_regions if str(r).strip().upper() == str(region).strip().upper()] or ([region] if region else [])


def filter_to_user_region(df: pd.DataFrame, region_col: str = "region") -> pd.DataFrame:
    """Filter a dataframe to the current user's region; no-op for super_admin.

    Comparison is case-insensitive (source data has inconsistent casing across
    tables) and never mutates the input in place, so it's safe to call on a
    dataframe returned from an @st.cache_data function. Regional users never
    see rows with a null/blank region -- deny by default.
    """
    if is_super_admin():
        return df
    region = current_region()
    if not region or region_col not in df.columns:
        return df.iloc[0:0]
    mask = df[region_col].astype(str).str.strip().str.upper() == region.strip().upper()
    return df[mask]
