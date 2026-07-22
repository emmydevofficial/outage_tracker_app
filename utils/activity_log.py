"""
Append-only audit trail of state-changing actions (logins, uploads, deletes,
user management changes). Never logs read-only page views -- that's a
deliberate scope decision, not an oversight.
"""

import streamlit as st
import pandas as pd
from sqlalchemy import text

from .db import get_engine


def log_activity(action: str, details: str = "") -> None:
    """Record one activity row using the current session's identity.

    Best-effort: a logging failure must never break the real operation it's
    attached to, so any DB error here is swallowed (not surfaced to the user).
    """
    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO activity_log (username, role, region, action, details)
                    VALUES (:username, :role, :region, :action, :details)
                """),
                {
                    "username": st.session_state.get("username"),
                    "role": st.session_state.get("role"),
                    "region": st.session_state.get("region"),
                    "action": action,
                    "details": details,
                },
            )
    except Exception:
        pass


def read_activity_log(
    limit: int = 500,
    action: str | None = None,
    username: str | None = None,
    start_date=None,
    end_date=None,
) -> pd.DataFrame:
    """Return the most recent activity rows, most recent first, with optional filters."""
    engine = get_engine()
    clauses = []
    params: dict = {"limit": limit}
    if action:
        clauses.append("action = :action")
        params["action"] = action
    if username:
        clauses.append("username = :username")
        params["username"] = username
    if start_date:
        clauses.append("created_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append("created_at < :end_date")
        params["end_date"] = end_date

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = text(f"""
        SELECT created_at, username, role, region, action, details
        FROM activity_log
        {where}
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    return pd.read_sql_query(query, engine, params=params)


def list_activity_actions() -> list:
    """Distinct action names seen so far, for building a filter dropdown."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT action FROM activity_log ORDER BY action")).fetchall()
    return [r[0] for r in rows]
