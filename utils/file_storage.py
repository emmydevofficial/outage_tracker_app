"""
Persists a copy of every uploaded file to disk and tracks it in the
uploaded_files table so a Super Admin can see and download it later.

Files are kept for 90 days by default (see EXPIRY_DAYS). There is no
persistent background scheduler in a Streamlit app, so expiry is enforced
lazily: purge_expired_files() is called whenever the Activity Log page loads,
and can also be run standalone (see purge_expired_uploads.py) via a real OS
cron/Task Scheduler for exact-day enforcement if that's wanted.
"""

from pathlib import Path
from uuid import uuid4
from datetime import timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import text

from .db import get_engine

EXPIRY_DAYS = 90
UPLOAD_DIR = Path(__file__).parent.parent / "uploaded_files"
UPLOAD_DIR.mkdir(exist_ok=True)


def save_uploaded_file(uploaded_file, source_page: str, region: str | None, exempt_from_expiry: bool = False) -> dict:
    """Write an uploaded file's bytes to disk and register it for the directory.

    uploaded_file is a Streamlit UploadedFile (has .name and .getvalue()).
    Returns the metadata row as a dict.
    """
    original_filename = uploaded_file.name
    data = uploaded_file.getvalue()
    stored_name = f"{uuid4().hex}_{original_filename}"
    stored_path = UPLOAD_DIR / stored_name
    stored_path.write_bytes(data)

    username = st.session_state.get("username")

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO uploaded_files
                    (username, region, source_page, original_filename, stored_path,
                     file_size_bytes, exempt_from_expiry, expires_at)
                VALUES
                    (:username, :region, :source_page, :original_filename, :stored_path,
                     :file_size_bytes, :exempt_from_expiry, now() + make_interval(days => :expiry_days))
                RETURNING id, username, region, source_page, original_filename, stored_path,
                          file_size_bytes, exempt_from_expiry, uploaded_at, expires_at, deleted_at
            """),
            {
                "username": username,
                "region": region,
                "source_page": source_page,
                "original_filename": original_filename,
                "stored_path": str(stored_path),
                "file_size_bytes": len(data),
                "exempt_from_expiry": exempt_from_expiry,
                "expiry_days": EXPIRY_DAYS,
            },
        ).mappings().fetchone()
    return dict(row)


def list_uploaded_files() -> pd.DataFrame:
    engine = get_engine()
    query = text("""
        SELECT id, username, region, source_page, original_filename, stored_path,
               file_size_bytes, exempt_from_expiry, uploaded_at, expires_at, deleted_at
        FROM uploaded_files
        ORDER BY uploaded_at DESC
    """)
    return pd.read_sql_query(query, engine)


def purge_expired_files() -> int:
    """Delete the physical file for every expired, non-exempt, not-yet-deleted row.

    The database row is never removed (audit history survives), only the
    bytes on disk and the deleted_at stamp. Returns the number purged.
    """
    engine = get_engine()
    with engine.begin() as conn:
        expired = conn.execute(
            text("""
                SELECT id, stored_path FROM uploaded_files
                WHERE expires_at < now() AND deleted_at IS NULL AND NOT exempt_from_expiry
            """)
        ).fetchall()

        purged = 0
        for row_id, stored_path in expired:
            try:
                Path(stored_path).unlink(missing_ok=True)
            except Exception:
                pass
            conn.execute(
                text("UPDATE uploaded_files SET deleted_at = now() WHERE id = :id"),
                {"id": row_id},
            )
            purged += 1
    return purged


def get_file_bytes(stored_path: str) -> bytes | None:
    """Read a stored file's bytes for a download button; None if already purged/missing."""
    path = Path(stored_path)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception:
        return None
