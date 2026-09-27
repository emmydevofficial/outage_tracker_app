"""
### FILE: views/monthly_load_review.py
Super Admin only: upload the ten TCC "Monthly Load Data" workbooks, store
them, and generate the operations review as HTML and PDF.

Adapted from the tested module in _incoming/monthly_review_module/ --
parser.py/metrics.py/narrative.py/render.py/svgcharts.py are copied
verbatim (their logic is not touched here). This file only wires the
module into this app's own conventions: utils.db.get_engine() instead of
a second connection string, .env instead of st.secrets, and
require_super_admin()/log_activity() to match every other admin page.

Every pull/upload is safe to repeat: mlr_upload_batch has a
UNIQUE(period, region) constraint, so store.save_month() deletes and
re-inserts that region's rows for the month instead of duplicating them --
re-uploading July twice still leaves exactly 10 rows for July, one per
region, not 20.

Env vars used (all optional -- without [AI_*] the paragraphs are written
by rule, and the report still generates in full):
    AI_PROVIDER = "anthropic" or "openai"
    AI_API_KEY  = "..."
    AI_MODEL    = "claude-sonnet-5" (Anthropic) or e.g. "gpt-5" (OpenAI)
"""
import calendar
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monthly_report.render import compute_report, write_text, render_html, html_to_pdf  # noqa: E402
from monthly_report import store  # noqa: E402
from monthly_report import narrative  # noqa: E402
from monthly_report import chat as mlr_chat  # noqa: E402
from monthly_report.parser import KNOWN_REGIONS, parse_workbook  # noqa: E402

SECTION_LABELS = {
    "lede": "Summary", "capacity": "Capacity out", "loading": "Transformer loading",
    "n1": "N-1 screen", "voltage": "Lines and voltage", "boundary": "Boundary lines",
    "timing": "Peak timing", "feeders": "Feeders", "energy": "Energy meters",
    "data": "State of the returns",
}

from utils.auth import require_super_admin
from utils.db import get_engine
from utils.activity_log import log_activity
from utils.branding import page_header

require_super_admin()
page_header("Monthly Load Review", "33kV Feeder Network · TCC Load Returns")
st.caption(
    "Upload the ten TCC Monthly Load Data workbooks for a month, review the generated "
    "operations report, then save it. Re-uploading a region for the same month replaces "
    "that region's rows -- it never creates duplicates."
)


def show_html(html: str, height: int = 1100):
    """st.iframe on newer Streamlit, components.html on older. Report text is HTML-escaped by the template."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        components.html(html, height=height, scrolling=True)


def prev_period(p: dt.date) -> dt.date:
    return (p.replace(day=1) - dt.timedelta(days=1)).replace(day=1)


engine = get_engine()
user = st.session_state.get("username")

tab_new, tab_history = st.tabs(["New month", "Past reports"])

with tab_new:
    c1, c2 = st.columns(2)
    today = dt.date.today()
    last = prev_period(today)
    year = c1.number_input("Year", 2024, 2100, last.year)
    month = c2.selectbox("Month", range(1, 13), index=last.month - 1, format_func=lambda m: calendar.month_name[m])
    period = dt.date(int(year), int(month), 1)

    uploads = st.file_uploader("Drop the TCC workbooks here (one per region)", type=["xlsx"], accept_multiple_files=True)

    ai_provider = os.getenv("AI_PROVIDER")
    ai_api_key = os.getenv("AI_API_KEY")
    ai_model = os.getenv("AI_MODEL")
    use_ai = st.toggle(
        f"Let {'Claude' if ai_provider == 'anthropic' else 'OpenAI'} write the paragraphs" if ai_api_key else
        "Let AI write the paragraphs (no AI_API_KEY set in .env)",
        value=bool(ai_api_key), disabled=not ai_api_key,
        help="Figures are always calculated in Python. The AI only writes the sentences, and any number "
             "it uses is checked against the data. Without a key the paragraphs are written by rule.",
    )

    if uploads:
        import openpyxl
        from monthly_report.parser import detect_region, header_snippet

        tmp = Path(tempfile.mkdtemp())
        detected = []  # (name, path, region_or_None, source)
        for u in uploads:
            p = tmp / u.name
            p.write_bytes(u.getbuffer())
            wb = openpyxl.load_workbook(p, data_only=True)
            region, source = detect_region(p, wb)
            if region is None and ai_api_key:
                region = narrative.guess_region(u.name, header_snippet(wb), ai_provider or "anthropic", ai_api_key, ai_model)
                if region:
                    source = "AI guess"
            detected.append((u.name, p, region, source))

        st.write("**Confirm each file's region** before continuing -- override anything that looks wrong.")
        confirmed = {}
        for name, p, region, source in detected:
            c1, c2, c3 = st.columns([3, 2, 2])
            c1.write(name)
            options = [""] + KNOWN_REGIONS
            picked = c2.selectbox(
                "Region", options, index=options.index(region) if region in options else 0,
                key=f"region_{name}", label_visibility="collapsed",
            )
            c3.caption(source if region else "unresolved -- pick a region")
            if picked:
                confirmed[name] = (p, picked)

        if len(confirmed) < len(detected):
            st.warning(f"{len(detected) - len(confirmed)} of {len(detected)} file(s) still need a region picked above.")
            st.stop()

        files = [(region, p) for p, region in confirmed.values()]
        uploaded_regions = sorted({region for _, region in confirmed.values()})
        scope = st.radio("Report for", ["All uploaded regions", "Selected regions"], horizontal=True)
        chosen = uploaded_regions
        if scope == "Selected regions":
            chosen = st.multiselect("Regions", uploaded_regions, default=uploaded_regions[:1])
            if not chosen:
                st.stop()

        build_key = (
            tuple(sorted((name, region) for name, (_, region) in confirmed.items())),
            period.isoformat(), scope, tuple(sorted(chosen)) if scope == "Selected regions" else None,
        )
        if st.session_state.get("mlr_draft_key") != build_key:
            with st.spinner("Reading the workbooks and writing the review..."):
                prev = store.load_month(engine, prev_period(period))
                mult, price = store.meter_settings(engine)
                report_data = compute_report(
                    files, period.year, period.month,
                    regions=None if scope == "All uploaded regions" else chosen,
                    prev_rows=prev or None, multipliers=mult, price_per_kwh=price or None,
                )
                text, notes, narrative_source = write_text(
                    report_data, api_key=ai_api_key if use_ai else None,
                    model=ai_model if use_ai else None, provider=ai_provider or "anthropic",
                )
                html = render_html(report_data, text, notes, narrative_source)
            st.session_state["mlr_draft_key"] = build_key
            st.session_state["mlr_draft"] = {
                "report_data": report_data, "text": dict(text), "original_text": dict(text),
                "notes": notes, "narrative_source": narrative_source, "model": ai_model if use_ai else "rules",
                "edits": [], "html": html, "prev_available": bool(prev),
                "chat_history": [], "chat_log": [], "chat_pending": None, "chat_user_messages": 0,
                "chat_usage_total": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            }
        draft = st.session_state["mlr_draft"]
        report_data = draft["report_data"]

        found = sorted({f["region"] for f in report_data["files"]})
        st.write(f"**Regions found:** {', '.join(found)}  ·  **rows:** {len(report_data['rows']):,}  ·  **cells corrected:** {len(report_data['issues']):,}")
        if len(found) < 10:
            st.warning(f"Only {len(found)} of 10 regions uploaded. The review will cover these regions only.")
        if not draft["prev_available"]:
            st.info(f"No returns stored for {calendar.month_name[prev_period(period).month]}. This month's meter readings become the baseline; MWh starts next month.")

        with st.expander("Corrections made on the way in"):
            st.dataframe(pd.DataFrame(report_data["issues"]), use_container_width=True, hide_index=True)
        for n in draft["notes"]:
            st.caption(n)

        show_html(draft["html"])

        with st.expander("Edit report text"):
            st.caption(
                "Wording only -- figures, tables and charts always come from the data. "
                "To fix a wrong figure, correct the workbook and re-upload."
            )
            edited = {}
            for section, label in SECTION_LABELS.items():
                edited[section] = st.text_area(label, draft["text"][section], key=f"mlr_text_{section}", height=100)
            b1, b2 = st.columns(2)
            if b1.button("Apply text changes"):
                changed = {s: v for s, v in edited.items() if v != draft["text"][s]}
                for section, new_val in changed.items():
                    draft["edits"].append({
                        "section": section, "old": draft["text"][section], "new": new_val,
                        "source": "manual", "status": "accepted", "reason": None,
                        "by": user, "at": dt.datetime.now().isoformat(),
                    })
                    draft["text"][section] = new_val
                if changed:
                    draft["html"] = render_html(report_data, draft["text"], draft["notes"], draft["narrative_source"])
                    st.rerun()
            if b2.button("Reset to original text"):
                for section in SECTION_LABELS:
                    if draft["text"][section] != draft["original_text"][section]:
                        draft["edits"].append({
                            "section": section, "old": draft["text"][section], "new": draft["original_text"][section],
                            "source": "reset", "status": "accepted", "reason": None,
                            "by": user, "at": dt.datetime.now().isoformat(),
                        })
                draft["text"] = dict(draft["original_text"])
                draft["html"] = render_html(report_data, draft["text"], draft["notes"], draft["narrative_source"])
                st.rerun()

        with st.expander("Chat about this report"):
            if not ai_api_key:
                st.info("Chat needs an Anthropic API key -- set AI_API_KEY in .env.")
            elif draft.get("chat_user_messages", 0) >= mlr_chat.MAX_CHAT_MESSAGES:
                st.warning(f"This draft has reached its {mlr_chat.MAX_CHAT_MESSAGES}-message chat limit.")
            else:
                for entry in draft["chat_log"]:
                    with st.chat_message(entry["role"]):
                        st.write(entry["text"])
                        if entry.get("looked_up"):
                            st.caption("Looked up: " + ", ".join(entry["looked_up"]))

                pending = draft["chat_pending"]
                if pending:
                    st.markdown(f"**Proposed rewrite -- {SECTION_LABELS[pending['section']]}**")
                    st.caption(pending["reason"])
                    pc1, pc2 = st.columns(2)
                    pc1.text_area("Current", pending["old_text"], height=100, disabled=True, key="mlr_chat_old")
                    pc2.text_area("Proposed", pending["new_text"], height=100, disabled=True, key="mlr_chat_new")
                    ac1, ac2 = st.columns(2)
                    if ac1.button("Accept", type="primary", key="mlr_chat_accept"):
                        draft["edits"].append({
                            "section": pending["section"], "old": pending["old_text"], "new": pending["new_text"],
                            "source": "chat", "status": "accepted", "reason": pending["reason"],
                            "by": user, "at": dt.datetime.now().isoformat(),
                        })
                        draft["text"][pending["section"]] = pending["new_text"]
                        draft["html"] = render_html(report_data, draft["text"], draft["notes"], draft["narrative_source"])
                        draft["chat_history"].append({"role": "user", "content": "Accepted."})
                        draft["chat_log"].append({"role": "user", "text": "(accepted the proposed rewrite)"})
                        draft["chat_pending"] = None
                        st.rerun()
                    if ac2.button("Reject", key="mlr_chat_reject"):
                        draft["edits"].append({
                            "section": pending["section"], "old": pending["old_text"], "new": pending["new_text"],
                            "source": "chat", "status": "rejected", "reason": pending["reason"],
                            "by": user, "at": dt.datetime.now().isoformat(),
                        })
                        draft["chat_history"].append({"role": "user", "content": "Rejected."})
                        draft["chat_log"].append({"role": "user", "text": "(rejected the proposed rewrite)"})
                        draft["chat_pending"] = None
                        st.rerun()

                if not pending:
                    msg = st.chat_input("Ask about the report, or ask for a rewrite...")
                    if msg:
                        with st.spinner("Claude is checking the report..."):
                            result = mlr_chat.run_turn(report_data, draft["text"], draft["chat_history"], msg, ai_api_key, ai_model)
                        draft["chat_history"] = result["messages"]
                        draft["chat_user_messages"] = draft.get("chat_user_messages", 0) + 1
                        draft["chat_log"].append({"role": "user", "text": msg})
                        draft["chat_log"].append({
                            "role": "assistant", "text": result["reply"],
                            "looked_up": [t["name"] for t in result["tool_calls"]] or None,
                        })
                        draft["chat_pending"] = result["proposal"]
                        for k, v in result["usage"].items():
                            draft["chat_usage_total"][k] += v
                        st.rerun()

                if draft["chat_log"]:
                    tot = draft["chat_usage_total"]
                    if st.button("Clear chat", key="mlr_chat_clear"):
                        draft["chat_history"], draft["chat_log"], draft["chat_pending"] = [], [], None
                        st.rerun()
                    st.caption(
                        f"Tokens this draft -- input: {tot['input_tokens']:,}, output: {tot['output_tokens']:,}, "
                        f"cache read: {tot['cache_read_input_tokens']:,}, cache write: {tot['cache_creation_input_tokens']:,}"
                    )

        name = f"Load_Returns_Review_{period:%Y_%m}_" + report_data["scope"].replace(" ", "_")
        d1, d2, d3 = st.columns(3)
        d1.download_button("Download HTML", draft["html"], f"{name}.html", "text/html")
        try:
            d2.download_button("Download PDF", html_to_pdf(draft["html"]), f"{name}.pdf", "application/pdf")
        except Exception as e:
            d2.caption(f"PDF needs Playwright and Chromium on the server ({type(e).__name__}).")
        if d3.button("Save data and report", type="primary"):
            store.save_month(engine, period, report_data["rows"], report_data["issues"], report_data["files"], user)
            rid = store.save_report(
                engine, period, draft["html"], report_data["f"],
                draft["model"], draft["notes"], user,
                original_text=draft["original_text"], final_text=draft["text"],
            )
            store.save_report_edits(engine, rid, draft["edits"])
            log_activity(
                "upload_monthly_review",
                f"{calendar.month_name[period.month]} {period.year} -- regions: {', '.join(found)}, "
                f"rows: {len(report_data['rows']):,}, report #{rid}",
            )
            st.success(f"Saved {len(report_data['rows']):,} rows for {calendar.month_name[period.month]} {period.year} and report #{rid}.")

with tab_history:
    reps = store.list_reports(engine)
    if not reps:
        st.write("No reports saved yet.")
    else:
        df = pd.DataFrame(reps)
        pick = st.selectbox("Report", df.id, format_func=lambda i: f"#{i} · {df.set_index('id').loc[i, 'period']:%B %Y} · "
                                                                    f"{df.set_index('id').loc[i, 'created_at']:%d %b %H:%M}")
        html = store.get_report_html(engine, int(pick))
        show_html(html)
        st.download_button("Download HTML", html, f"report_{pick}.html", "text/html")

        edits = store.list_report_edits(engine, int(pick))
        with st.expander(f"Edit history ({len(edits)})"):
            if not edits:
                st.write("No text edits on this report.")
            else:
                st.dataframe(pd.DataFrame(edits), use_container_width=True, hide_index=True)
