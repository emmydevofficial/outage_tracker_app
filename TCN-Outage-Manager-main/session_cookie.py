"""Persistent, cross-tab login via a signed browser cookie.

Deliberately parallel to (but independent from) outage_tracker-main's
utils/session_cookie.py -- same pattern, own cookie name and signing secret,
since this app's login is fully separate from the 33kV app's. See that
module's docstring for the full rationale.
"""
import os

import streamlit as st
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from streamlit_cookies_controller import CookieController

COOKIE_NAME = "tcn_outage_session"
SESSION_MAX_AGE_SECONDS = 3600  # 1 hour, sliding -- resets on every active render


@st.cache_resource
def _serializer() -> URLSafeTimedSerializer:
    secret = os.getenv("SESSION_SECRET_KEY")
    if not secret:
        raise RuntimeError("SESSION_SECRET_KEY is not set")
    return URLSafeTimedSerializer(secret, salt="tcn-outage-session")


def _controller() -> CookieController:
    # CookieController registers itself as a Streamlit widget on construction
    # (it writes st.session_state[key] internally) -- constructing it more
    # than once in the same script run raises "cannot be modified after the
    # widget ... is instantiated". So build it at most once per session and
    # cache *that instance* in session_state under our own separate key;
    # Streamlit's own component protocol keeps its cookie data fresh across
    # reruns without needing to reconstruct the Python wrapper each time.
    cache_key = "_tcn_outage_cookie_controller"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = CookieController(key="tcn_outage_cookies")
    return st.session_state[cache_key]


def issue_session_cookie(username: str) -> None:
    """Sign a fresh token and (re)write the cookie with a full new expiry window."""
    token = _serializer().dumps(username)
    _controller().set(COOKIE_NAME, token, max_age=SESSION_MAX_AGE_SECONDS)


def read_session_username() -> str | None:
    """Return the username from a valid, unexpired session cookie, else None."""
    token = _controller().get(COOKIE_NAME)
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def clear_session_cookie() -> None:
    _controller().remove(COOKIE_NAME)
