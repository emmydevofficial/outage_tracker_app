"""Talks to the per-region Apps Script Web Apps.

Each of the 10 TCN regions has its own "REPORTING TEMPLATES" Google Sheet
and its own deployed Apps Script Web App. Their URLs live in the
web_app_settings table (managed on the Web App Settings page), not in
.env -- there are too many for that. This module looks the right URL up
per region and does the list-tabs / pull-and-save round trips.
"""
import os

import requests

import db

_TRIGGER_TOKEN = os.getenv("APPS_SCRIPT_TRIGGER_TOKEN", "")
# Kept only as a fallback for ABUJA while the settings table is being
# populated -- once every region's URL is entered on the settings page
# this is unused.
_ENV_FALLBACK_URL = os.getenv("APPS_SCRIPT_WEB_APP_URL", "")


def _url_for(region: str) -> str | None:
    url = db.get_web_app_url(region)
    if url:
        return url
    if region and region.upper() == "ABUJA" and _ENV_FALLBACK_URL:
        return _ENV_FALLBACK_URL
    return None


def call_apps_script(region: str, action: str, **params) -> dict:
    """One GET to a region's Apps Script Web App. Never raises -- a bad
    URL / network error / missing config comes back as {"ok": False,
    "error": ...} so callers can show a clean message."""
    url = _url_for(region)
    if not url:
        return {"ok": False, "error": f"No Web App URL configured for {region}. Set it on the Web App Settings page."}
    try:
        resp = requests.get(
            url,
            params={"token": _TRIGGER_TOKEN, "action": action, "region": region, **params},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_sheet_tabs(region: str) -> dict:
    """{"ok": True, "tabs": [...]} or {"ok": False, "error": ...}."""
    return call_apps_script(region, "list_tabs")


def pull_and_save_outage_requests(region: str, tab_name: str) -> dict:
    """Pull one tab's Outage Schedule rows and persist them -- opens a
    sync_runs row, upserts by sync_id (additive and permanent: inserts new
    rows, updates matching ones, never removes or hides anything), closes
    the sync_runs row. Returns a status dict."""
    result = call_apps_script(region, "pull", type="outage_request", tab=tab_name)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "unknown error")}

    rows = result.get("rows", [])
    sheet_id = result.get("sheet_id", "")
    tab = result.get("tab_name", tab_name)

    run_id = db.start_sync_run(
        region=region, report_type="outage_request",
        sheet_id=sheet_id, tab_name=tab, rows_received=len(rows),
    )
    try:
        upsert = db.upsert_outage_requests(rows, run_id=run_id, region=region, sheet_id=sheet_id, tab_name=tab)
        db.finish_sync_run(
            run_id, inserted=upsert["inserted"], updated=upsert["updated"],
            rejected=len(upsert["rejected"]), status="success",
        )
        return {"ok": True, "sync_run_id": run_id, **upsert}
    except Exception as e:
        db.finish_sync_run(run_id, inserted=0, updated=0, rejected=0, status="failed", error_message=str(e))
        return {"ok": False, "error": str(e)}


def pull_and_save_incident_reports(region: str, tab_name: str) -> dict:
    """Pull one tab's Incident Report rows and persist them -- same
    additive-and-permanent pattern as pull_and_save_outage_requests."""
    result = call_apps_script(region, "pull", type="incident_report", tab=tab_name)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "unknown error")}

    rows = result.get("rows", [])
    sheet_id = result.get("sheet_id", "")
    tab = result.get("tab_name", tab_name)

    run_id = db.start_sync_run(
        region=region, report_type="incident_report",
        sheet_id=sheet_id, tab_name=tab, rows_received=len(rows),
    )
    try:
        upsert = db.upsert_incident_reports(rows, run_id=run_id, region=region, sheet_id=sheet_id, tab_name=tab)
        db.finish_sync_run(
            run_id, inserted=upsert["inserted"], updated=upsert["updated"],
            rejected=len(upsert["rejected"]), status="success",
        )
        return {"ok": True, "sync_run_id": run_id, **upsert}
    except Exception as e:
        db.finish_sync_run(run_id, inserted=0, updated=0, rejected=0, status="failed", error_message=str(e))
        return {"ok": False, "error": str(e)}


def pull_and_save_daily_max_min(region: str, tab_name: str) -> dict:
    """Pull one tab's Daily Max/Min rows and persist them -- same
    additive-and-permanent pattern as the other two report types."""
    result = call_apps_script(region, "pull", type="daily_max_min", tab=tab_name)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "unknown error")}

    rows = result.get("rows", [])
    sheet_id = result.get("sheet_id", "")
    tab = result.get("tab_name", tab_name)

    run_id = db.start_sync_run(
        region=region, report_type="daily_max_min",
        sheet_id=sheet_id, tab_name=tab, rows_received=len(rows),
    )
    try:
        upsert = db.upsert_daily_max_min(rows, run_id=run_id, region=region, sheet_id=sheet_id, tab_name=tab)
        db.finish_sync_run(
            run_id, inserted=upsert["inserted"], updated=upsert["updated"],
            rejected=len(upsert["rejected"]), status="success",
        )
        return {"ok": True, "sync_run_id": run_id, **upsert}
    except Exception as e:
        db.finish_sync_run(run_id, inserted=0, updated=0, rejected=0, status="failed", error_message=str(e))
        return {"ok": False, "error": str(e)}


def classify_work(description: str | None, remarks: str | None) -> str:
    """Best-effort category for an outage request.

    The Outage Schedule sheet has NO planned/emergency column of its own
    (unlike the Incident Report sheet, which does) -- this is inferred
    from the free-text "Description of Work" + "Remarks/Notes", so it's a
    convenience for filtering, not an authoritative field. Anything that
    doesn't clearly match falls in "Other".
    """
    text = f"{description or ''} {remarks or ''}".upper()
    emergency_markers = ("EMERGENCY", "FAULT", "BURNT", "TRIP", "FAILURE", "FAILED", "URGENT", "DEFECT")
    planned_markers = (
        "PLANNED", "MAINTENANCE", "VEGETATION", "ACCEPTANCE TEST", "INSPECTION",
        "REPLACEMENT", "RELOCATION", "INSTALLATION", "CALIBRATION", "PROJECT",
        "ROUTINE", "SCHEDULED", "UPGRADE", "TEST",
    )
    if any(m in text for m in emergency_markers):
        return "Emergency"
    if any(m in text for m in planned_markers):
        return "Planned"
    return "Other"
