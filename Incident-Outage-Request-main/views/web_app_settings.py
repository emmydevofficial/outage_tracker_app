"""Web App Settings -- store each region's Apps Script Web App URL.
Admin-only (kept as defence-in-depth; the router already leaves this out
of the menu for non-admins). Rendered by app.py's router."""
import pandas as pd
import streamlit as st

import db
from auth import require_admin
from activity_log import log_activity
from branding import page_header, one_indexed

page_header("Web App Settings", "Apps Script Web App URL per region")
require_admin()

st.markdown(
    "Paste each region's Apps Script **Web App URL** here (from Deploy → New "
    "deployment → Web app, inside that region's *REPORTING TEMPLATES* sheet). "
    "The Pull Data page uses these."
)

settings_df = db.list_web_app_settings()

edited = st.data_editor(
    settings_df[["region", "web_app_url"]].rename(columns={"region": "Region", "web_app_url": "Web App URL"}),
    use_container_width=True,
    hide_index=True,
    disabled=["Region"],
    key="web_app_editor",
    column_config={
        "Web App URL": st.column_config.TextColumn(width="large", help="https://script.google.com/macros/s/.../exec"),
    },
)

def _clean(v) -> str:
    # data_editor returns NaN (a float) for an emptied cell -- NaN is
    # truthy, so `v or ""` doesn't guard it; check explicitly.
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


if st.button("💾 Save URLs", type="primary"):
    changes = 0
    original = {r: _clean(u) for r, u in zip(settings_df["region"], settings_df["web_app_url"])}
    for _, row in edited.iterrows():
        region = row["Region"]
        new_url = _clean(row["Web App URL"])
        if new_url != original.get(region, ""):
            db.upsert_web_app_url(region, new_url, updated_by=st.session_state.get("username"))
            changes += 1
    if changes:
        log_activity("update_web_app_settings", f"Updated {changes} region URL(s)")
        st.success(f"Saved {changes} change(s).")
        st.rerun()
    else:
        st.info("No changes to save.")

st.divider()
st.caption("Last updated per region:")
st.dataframe(
    one_indexed(settings_df.rename(columns={
        "region": "Region", "web_app_url": "Web App URL",
        "updated_at": "Updated At", "updated_by": "Updated By",
    })),
    use_container_width=True,
)
