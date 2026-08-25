"""Shared TCN design system for Load & Outage Analytics.

Ported from TCN-Outage-Manager-main/app.py so both apps share the same
visual language (fonts, colors, KPI cards, chart styling) -- Load & Outage
Analytics covers 33kV feeder load/outages, TCN Grid Outage Manager covers
330/132kV equipment outages, but both are TCN apps and should look alike.
"""
import base64
import time
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).parent.parent
LOGO = BASE_DIR / "assets" / "tcn_logo.png"
APP_BG = BASE_DIR / "assets" / "tcn_background.png"
LOGIN_BG = BASE_DIR / "assets" / "login_bg.png"

TCN_RED = "#c81e28"
TCN_BLUE = "#1e3a7a"
TCN_COLORS = [TCN_BLUE, TCN_RED, "#1F6C9F", "#E06E6A", "#7DA3D8", "#956400", "#346538", "#8A8580"]
TCN_RED_SCALE = [[0, "#FBEAEA"], [0.5, "#E06E6A"], [1, TCN_RED]]

TCN_CHART_LAYOUT = dict(
    font=dict(family="Source Sans Pro, sans-serif", size=14, color="#37352F"),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=48, b=10),
    title_font=dict(size=17, color="#37352F"),
    bargap=0.25,
    hoverlabel=dict(
        bgcolor="rgba(30, 47, 92, 0.92)",
        bordercolor="rgba(255,255,255,0.15)",
        font=dict(size=14, color="white"),
    ),
)


def _b64(path: Path) -> str:
    try:
        return base64.b64encode(path.read_bytes()).decode()
    except Exception:
        return ""


def _style_chart(fig):
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="rgba(55,53,47,0.08)", zeroline=False)
    return fig


def _eyebrow(text, bg="#E8EEF7", fg=TCN_BLUE):
    st.markdown(
        f'<span style="display:inline-block;font-size:0.8rem;font-weight:700;'
        f'letter-spacing:0.06em;text-transform:uppercase;padding:4px 11px;'
        f'border-radius:5px;background:{bg};color:{fg};">{text}</span>',
        unsafe_allow_html=True,
    )


ICONS = {
    "bolt": '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "power": '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/>',
    "building": '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "pulse": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "tower": '<path d="M12 2v20"/><path d="M6 22l6-14 6 14"/><path d="M8 12h8"/>',
    "chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
}


def kpi_card(label, value, unit="", icon="bolt", color=TCN_BLUE):
    unit_html = (
        f'<span style="font-size:0.85rem;font-weight:600;color:var(--text-tertiary);margin-left:3px;">{unit}</span>'
        if unit else ""
    )
    r, g, b = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return f"""<div class="kpi-card"><div class="kpi-card-inner">
        <div class="kpi-left">
            <div class="kpi-icon" style="background:linear-gradient(135deg,rgba({r},{g},{b},0.12),rgba({r},{g},{b},0.05));">
                <svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONS[icon]}</svg>
            </div>
            <div><div class="kpi-label">{label}</div><div class="kpi-value">{value}{unit_html}</div></div>
        </div>
    </div></div>"""


def kpi_grid(cards):
    n = len(cards)
    st.markdown(
        f'<div class="kpi-grid" style="grid-template-columns: repeat({n}, 1fr);">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def inject_css():
    bg64 = _b64(APP_BG)
    bg_rule = (
        f'.stApp {{ background: linear-gradient(rgba(248,249,252,0.94), rgba(248,249,252,0.94)), '
        f'url("data:image/png;base64,{bg64}") center/cover fixed; }}'
        if bg64 else ".stApp { background: #f8f9fc; }"
    )
    st.markdown(f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=Source+Sans+Pro:wght@400;600;700&family=JetBrains+Mono:wght@500;600&display=swap');
    :root {{
        --text-primary: #37352F;
        --text-secondary: #787774;
        --text-tertiary: #9B9A97;
        --tcn-red: {TCN_RED};
        --tcn-blue: {TCN_BLUE};
        --surface: #FFFFFF;
        --border: rgba(55,53,47,0.09);
        --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
    }}
    {bg_rule}
    html {{ font-size: 18px; }}
    html, body, [class*="css"] {{ font-family: 'Source Sans Pro', sans-serif; }}
    p, li, label {{ font-size: 1rem; line-height: 1.5; }}

    /* Sidebar */
    [data-testid="stSidebar"] {{
        background: linear-gradient(180deg, #0a1630 0%, #0e1c3d 45%, {TCN_BLUE} 140%);
        border-right: 1px solid rgba(255,255,255,0.06);
    }}
    [data-testid="stSidebar"] * {{ color: #E8ECF5 !important; }}
    [data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,0.15); }}
    [data-testid="stSidebarNav"] a span {{
        font-size: 0.95rem !important; font-weight: 600 !important;
    }}
    [data-testid="stSidebar"] small {{
        font-size: 0.85rem !important; font-weight: 600 !important;
    }}

    /* Filters header */
    .flt-header {{
        display: flex; align-items: center; gap: 0.7rem;
        margin: 1.1rem 0 0.9rem 0; padding: 0.85rem 0.9rem;
        background: linear-gradient(135deg, rgba(200,30,40,0.22), rgba(255,255,255,0.05) 65%);
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 14px;
    }}
    .flt-icon {{
        width: 34px; height: 34px; border-radius: 9px; flex-shrink: 0;
        display: flex; align-items: center; justify-content: center;
        background: linear-gradient(135deg, {TCN_RED}, #8f1620);
        box-shadow: 0 2px 8px rgba(200,30,40,0.4);
    }}
    .flt-icon svg {{ width: 17px; height: 17px; }}
    .flt-title {{ font-size: 1.15rem; font-weight: 700; line-height: 1.2; }}
    .flt-sub {{ font-size: 0.8rem; font-weight: 600; color: #9FB0D6 !important; letter-spacing: 0.04em; }}

    /* Filter widget labels */
    [data-testid="stSidebar"] .stMultiSelect label p,
    [data-testid="stSidebar"] .stDateInput label p {{
        font-size: 0.85rem !important; font-weight: 700 !important;
        text-transform: uppercase; letter-spacing: 0.09em;
        color: #9FB0D6 !important;
    }}

    /* Glassy inputs */
    [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] > div,
    [data-testid="stSidebar"] .stDateInput [data-baseweb="input"] {{
        background: rgba(255,255,255,0.07) !important;
        border: 1px solid rgba(255,255,255,0.16) !important;
        border-radius: 12px !important;
        transition: border-color 0.25s ease, background 0.25s ease, box-shadow 0.25s ease;
    }}
    [data-testid="stSidebar"] .stDateInput input {{ background: transparent !important; }}
    [data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] > div:hover,
    [data-testid="stSidebar"] .stDateInput [data-baseweb="input"]:hover {{
        border-color: rgba(255,255,255,0.38) !important;
        background: rgba(255,255,255,0.11) !important;
        box-shadow: 0 0 0 3px rgba(255,255,255,0.05);
    }}

    /* Sidebar buttons (Logout, cross-app link) -- give secondary buttons a
       visible background so light sidebar text doesn't sit on a light
       Streamlit-default button background until hover. */
    [data-testid="stSidebar"] .stButton > button,
    [data-testid="stSidebar"] .stLinkButton > a {{
        background: rgba(255,255,255,0.08) !important;
        border: 1px solid rgba(255,255,255,0.22) !important;
        border-radius: 9px !important;
        transition: background 0.2s ease, border-color 0.2s ease;
    }}
    [data-testid="stSidebar"] .stButton > button p,
    [data-testid="stSidebar"] .stLinkButton > a p,
    [data-testid="stSidebar"] .stLinkButton > a {{
        color: #E8ECF5 !important;
    }}
    [data-testid="stSidebar"] .stButton > button:hover,
    [data-testid="stSidebar"] .stLinkButton > a:hover {{
        background: rgba(255,255,255,0.18) !important;
        border-color: rgba(255,255,255,0.4) !important;
    }}
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {{
        background: {TCN_RED} !important;
        border: none !important;
    }}
    [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {{
        background: #a51822 !important;
    }}

    /* Tag pills */
    [data-testid="stSidebar"] span[data-baseweb="tag"] {{
        border-radius: 8px !important; font-weight: 600;
        border: none !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.15);
        transition: transform 0.2s var(--ease-spring);
        background: linear-gradient(135deg, #2a4a94, {TCN_BLUE}) !important;
    }}
    [data-testid="stSidebar"] span[data-baseweb="tag"]:hover {{ transform: translateY(-1px); }}

    /* KPI cards */
    .kpi-grid {{ display: grid; gap: 0.8rem; margin: 0.6rem 0 1.1rem 0; }}
    .kpi-card {{
        position: relative; background: var(--surface); border-radius: 14px;
        border: 1px solid var(--border);
        box-shadow: 0 1px 2px rgba(16,24,40,0.04), 0 4px 12px rgba(16,24,40,0.04);
        overflow: hidden;
        transition: transform 0.35s var(--ease-spring), box-shadow 0.35s ease;
    }}
    .kpi-card::before {{
        content: ""; position: absolute; inset: 0; border-radius: 14px; padding: 1px;
        background: linear-gradient(135deg, rgba(30,58,122,0.25), rgba(200,30,40,0.12), transparent 60%);
        -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
        -webkit-mask-composite: xor; mask-composite: exclude;
        pointer-events: none;
    }}
    .kpi-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 8px rgba(16,24,40,0.06), 0 12px 24px rgba(16,24,40,0.08); }}
    .kpi-card-inner {{ display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.1rem; }}
    .kpi-left {{ display: flex; align-items: center; gap: 0.75rem; }}
    .kpi-icon {{
        width: 40px; height: 40px; border-radius: 10px; flex-shrink: 0;
        display: flex; align-items: center; justify-content: center;
    }}
    .kpi-icon svg {{ width: 20px; height: 20px; }}
    .kpi-label {{
        font-size: 0.78rem; font-weight: 700; letter-spacing: 0.06em;
        text-transform: uppercase; color: var(--text-tertiary); margin-bottom: 3px;
    }}
    .kpi-value {{
        font-family: 'JetBrains Mono', monospace; font-size: 1.6rem;
        font-weight: 700; color: var(--text-primary); line-height: 1.15;
    }}

    /* Page header */
    .dash-header {{ display: flex; align-items: center; justify-content: space-between; margin: 0.4rem 0 0.2rem 0; }}
    .dash-title {{ font-size: 2.1rem; font-weight: 700; color: var(--text-primary); margin: 0; }}
    .dash-sub {{ font-size: 0.95rem; color: var(--text-secondary); }}
    .live-badge {{
        display: inline-flex; align-items: center; gap: 6px; font-size: 0.82rem; font-weight: 600;
        color: #346538; background: #EDF3EC; padding: 4px 11px; border-radius: 999px;
    }}
    .live-dot {{
        width: 7px; height: 7px; border-radius: 50%; background: #4CAF50;
        animation: pulse 1.8s infinite;
    }}
    @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.35; }} }}

    /* Tabs — bold pill navigation */
    .stTabs [data-baseweb="tab-list"] {{
        background: var(--surface);
        border-radius: 16px;
        padding: 6px;
        border: 1px solid var(--border);
        box-shadow: 0 1px 2px rgba(16,24,40,0.05), 0 4px 14px rgba(16,24,40,0.05);
        gap: 4px;
        flex-wrap: wrap;
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 11px;
        padding: 0.55rem 1.15rem;
        height: auto;
        background: transparent;
        transition: background 0.25s ease, box-shadow 0.25s ease,
                    transform 0.3s var(--ease-spring);
    }}
    .stTabs [data-baseweb="tab"] p {{
        font-weight: 700 !important;
        font-size: 1rem !important;
        letter-spacing: 0.015em;
        color: var(--text-secondary);
        transition: color 0.25s ease;
        white-space: nowrap;
    }}
    .stTabs [data-baseweb="tab"]:hover {{
        background: #F1F4FA;
        transform: translateY(-1px);
    }}
    .stTabs [data-baseweb="tab"]:hover p {{ color: var(--tcn-blue); }}
    .stTabs [aria-selected="true"] {{
        background: linear-gradient(135deg, {TCN_BLUE} 0%, #142a5c 100%) !important;
        box-shadow: 0 2px 10px rgba(30,58,122,0.35), inset 0 1px 0 rgba(255,255,255,0.12);
    }}
    .stTabs [aria-selected="true"]:hover {{ transform: none; }}
    .stTabs [aria-selected="true"] p {{ color: #FFFFFF !important; }}
    .stTabs [data-baseweb="tab-highlight"] {{ display: none; }}
    .stTabs [data-baseweb="tab-border"] {{ display: none; }}

    /* Buttons */
    .stButton > button[kind="primary"] {{
        background: {TCN_RED}; border: none; border-radius: 9px; font-weight: 600;
    }}
    header[data-testid="stHeader"] {{ background: transparent; }}

    /* Dataframe/table text -- st.dataframe draws its grid on canvas and reads
       these theme values as CSS custom properties, so they DO take effect
       even though the text itself isn't normal DOM markup. */
    [data-testid="stDataFrame"] {{
        --gdg-base-font-style: 600 15px !important;
        --gdg-header-font-style: 700 15px !important;
        --gdg-cell-horizontal-padding: 12px !important;
        --gdg-cell-vertical-padding: 8px !important;
    }}

    /* Credits / project team card */
    .credits-card {{
        background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
        padding: 1.8rem 2rem; margin: 1.6rem 0;
        box-shadow: 0 1px 2px rgba(16,24,40,0.04), 0 4px 12px rgba(16,24,40,0.04);
    }}
    .credits-title {{ font-size: 1.4rem; font-weight: 700; color: var(--text-primary); margin-bottom: 0.2rem; }}
    .credits-sub {{ font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 1.5rem; }}
    .credits-grid {{ display: grid; grid-template-columns: 1fr; gap: 1rem; }}
    @media (min-width: 900px) {{ .credits-grid {{ grid-template-columns: 1fr 1fr; }} }}
    .credit-row {{
        padding: 0.95rem 1.15rem; border-radius: 12px;
        background: linear-gradient(135deg, rgba(30,58,122,0.05), rgba(200,30,40,0.02));
        border-left: 3px solid var(--tcn-blue);
    }}
    .credit-row.credit-approved {{
        background: linear-gradient(135deg, rgba(200,30,40,0.06), rgba(200,30,40,0.02));
        border-left-color: var(--tcn-red);
    }}
    .credit-role {{
        font-size: 0.78rem; font-weight: 700; letter-spacing: 0.06em;
        text-transform: uppercase; color: var(--text-tertiary); margin-bottom: 4px;
    }}
    .credit-names {{
        font-size: 1.05rem; font-weight: 700; color: var(--text-primary); line-height: 1.5;
    }}
    </style>""", unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "33kV Feeder Network · Load · Outages · Analytics"):
    """Logo + title + tagline block, reused at the top of every page."""
    logo64 = _b64(LOGO)
    logo_html = (
        f'<div style="width:52px;height:52px;border-radius:13px;display:flex;align-items:center;'
        f'justify-content:center;background:linear-gradient(135deg,#E8EEF7,#FBEAEA);'
        f'border:1px solid rgba(55,53,47,0.08);">'
        f'<img src="data:image/png;base64,{logo64}" width="36"></div>'
        if logo64 else ""
    )
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:0.9rem;margin-bottom:0.4rem;">
        {logo_html}
        <div>
            <div style="font-size:1.6rem;font-weight:700;color:var(--text-primary);">{title}</div>
            <div style="font-size:0.85rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:var(--text-tertiary);">
                {subtitle}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


CREDITS = [
    ("Approved By", ["Engr. Godwin A. Aguiyi (GM)"]),
    ("Verified By", ["Engr. Gabriel Onuche", "Engr. Adejayan Adesanmi"]),
    ("Reviewed By", ["Engr. Kelechi Elohoanya", "Engr. Tayo Ogunmola"]),
    ("Supervised By", ["Engr. Matthew, Adedeji F."]),
    ("Developed By", ["Engr. Ibrahim Usman", "Engr. Kingsley Okpala", "Engr. Oluwaloni Emmanuel"]),
]


def credits_section():
    """Project sign-off / team card -- who approved, reviewed, and built this app."""
    rows_html = ""
    for role, names in CREDITS:
        row_class = "credit-row credit-approved" if role == "Approved By" else "credit-row"
        names_html = "<br>".join(names)
        rows_html += (
            f'<div class="{row_class}">'
            f'<div class="credit-role">{role}</div>'
            f'<div class="credit-names">{names_html}</div>'
            f'</div>'
        )
    st.markdown(f"""
    <div class="credits-card">
        <div class="credits-title">Project Team</div>        
        <div class="credits-grid">{rows_html}</div>
    </div>
    """, unsafe_allow_html=True)


def render_login_screen(verify_fn=None, subtitle="Load & Outage Analytics"):
    """Full-page centered login card, matching TCN Grid Outage Manager's login screen.

    Renders the logo, a headline below it, and a form with Username/Password + Sign In.
    If verify_fn is given, it's called as verify_fn(username, password) right
    after submit, inside the card, under a spinner (so the loading state is
    obvious instead of relying on Streamlit's thin top progress bar) -- its
    return value is passed back as verify_result. The caller still owns
    session_state / st.rerun() based on that result.

    Returns (username, password, submitted, verify_result).
    """
    bg64 = _b64(LOGIN_BG)
    logo64 = _b64(LOGO)
    bg_css = (
        f'background: linear-gradient(rgba(6,12,30,0.60), rgba(6,12,30,0.72)), '
        f'url("data:image/png;base64,{bg64}") center/cover fixed !important;'
        if bg64 else
        "background: linear-gradient(160deg, #0a1630, #1e3a7a) !important;"
    )
    st.markdown(f"""<style>
    html {{ font-size: 18px; }}
    .stApp {{ {bg_css} }}
    header[data-testid="stHeader"] {{ background: transparent; }}
    [data-testid="stSidebar"] {{ display: none; }}

    .login-logo {{
        width: 96px; height: 96px; margin: 1.6rem auto 0 auto;
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        background: rgba(255,255,255,0.94);
        border: 1px solid rgba(255,255,255,0.35);
        box-shadow:
            0 0 0 8px rgba(255,255,255,0.07),
            0 0 0 16px rgba(255,255,255,0.035),
            0 8px 32px rgba(200,30,40,0.35);
        animation: logo-glow 3.5s ease-in-out infinite;
    }}
    @keyframes logo-glow {{
        0%, 100% {{ box-shadow: 0 0 0 8px rgba(255,255,255,0.07), 0 0 0 16px rgba(255,255,255,0.035), 0 8px 32px rgba(200,30,40,0.35); }}
        50% {{ box-shadow: 0 0 0 8px rgba(255,255,255,0.10), 0 0 0 16px rgba(255,255,255,0.05), 0 8px 44px rgba(200,30,40,0.55); }}
    }}
    .login-logo img {{ width: 64px; }}

    .login-sub {{
        text-align: center; color: white; font-size: 1.9rem;
        font-weight: 800; letter-spacing: 0.02em; text-transform: uppercase;
        margin: 1.2rem 0 1.5rem 0;
        text-shadow: 0 2px 18px rgba(0,0,0,0.45);
    }}
    .login-sub .dot {{ color: {TCN_RED}; font-weight: 700; }}

    [data-testid="stForm"] {{
        position: relative;
        background: linear-gradient(160deg, rgba(22,35,70,0.62), rgba(14,24,50,0.55));
        backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
        border: 1px solid rgba(255,255,255,0.16);
        border-radius: 22px;
        padding: 2.6rem 2.4rem 2.2rem 2.4rem;
        box-shadow: 0 24px 64px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.12);
    }}
    [data-testid="stForm"] label p {{
        color: #C9D4EE !important; font-size: 0.85rem !important; font-weight: 700 !important;
        text-transform: uppercase; letter-spacing: 0.08em;
    }}
    [data-testid="stForm"] [data-testid="stTextInputRootElement"] {{
        background: rgba(255,255,255,0.09) !important;
        border: 1px solid rgba(255,255,255,0.18) !important;
        border-radius: 12px !important;
        min-height: 3.2rem !important;
        transition: border-color 0.25s ease, background 0.25s ease, box-shadow 0.25s ease;
    }}
    [data-testid="stForm"] [data-testid="stTextInputRootElement"]:hover {{
        border-color: rgba(255,255,255,0.4) !important;
        background: rgba(255,255,255,0.13) !important;
    }}
    [data-testid="stForm"] [data-testid="stTextInputRootElement"]:focus-within {{
        border-color: rgba(224,110,106,0.85) !important;
        box-shadow: 0 0 0 3px rgba(200,30,40,0.25) !important;
    }}
    [data-testid="stForm"] input {{ background: transparent !important; color: white !important; font-size: 1.2rem !important; font-weight: 500 !important; }}
    [data-testid="stForm"] input::placeholder {{ color: rgba(255,255,255,0.45) !important; }}

    [data-testid="stForm"] [data-testid="stTextInputRootElement"] button {{
        background: transparent !important; border: none !important;
        box-shadow: none !important;
    }}
    [data-testid="stForm"] [data-testid="stTextInputRootElement"] button svg {{ fill: rgba(255,255,255,0.6); }}

    [data-testid="stForm"] [data-testid="stFormSubmitButton"] button {{
        background: linear-gradient(135deg, {TCN_RED} 0%, #8f1620 100%) !important;
        border: none !important; border-radius: 12px !important;
        padding: 0.85rem 1rem !important; margin-top: 0.6rem;
        box-shadow: 0 4px 18px rgba(200,30,40,0.45), inset 0 1px 0 rgba(255,255,255,0.22);
        transition: transform 0.3s var(--ease-spring, ease), box-shadow 0.3s ease, filter 0.25s ease;
    }}
    [data-testid="stForm"] [data-testid="stFormSubmitButton"] button p {{
        color: white !important; font-weight: 700 !important;
        letter-spacing: 0.06em; text-transform: uppercase; font-size: 1.05rem !important;
    }}
    [data-testid="stForm"] [data-testid="stFormSubmitButton"] button:hover {{
        transform: translateY(-2px);
        box-shadow: 0 8px 26px rgba(200,30,40,0.6), inset 0 1px 0 rgba(255,255,255,0.25);
        filter: brightness(1.06);
    }}
    [data-testid="stForm"] [data-testid="stFormSubmitButton"] button:active {{ transform: translateY(0); }}

    .login-footer {{
        text-align: center; margin-top: 1.1rem;
        color: rgba(255,255,255,0.6); font-size: 0.85rem; font-weight: 600; letter-spacing: 0.06em;
    }}
    .login-footer b {{ color: rgba(255,255,255,0.65); }}

    /* Verifying-credentials spinner -- default theme text/icon color is dark,
       invisible against this dark card/background */
    [data-testid="stSpinner"], [data-testid="stSpinner"] * {{
        color: #E8ECF5 !important;
    }}
    [data-testid="stSpinner"] svg {{
        fill: #E8ECF5 !important;
        stroke: #E8ECF5 !important;
    }}
    </style>""", unsafe_allow_html=True)

    _, mid, _ = st.columns([0.6, 2, 0.6])
    with mid:
        if logo64:
            st.markdown(
                f'<div class="login-logo"><img src="data:image/png;base64,{logo64}"></div>',
                unsafe_allow_html=True,
            )
        st.markdown(f'<p class="login-sub">{subtitle}</p>', unsafe_allow_html=True)
        with st.form("login"):
            username = st.text_input("Username", placeholder="Enter your username")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Sign In", use_container_width=True)
            error = st.session_state.pop("_login_error", None)
            if error:
                st.error(error)
        verify_result = None
        if submitted and verify_fn is not None:
            with st.spinner("🔐 Verifying credentials..."):
                start = time.monotonic()
                verify_result = verify_fn(username, password)
                # guarantee the spinner is on screen long enough to actually
                # notice, even when auth resolves near-instantly
                remaining = 0.5 - (time.monotonic() - start)
                if remaining > 0:
                    time.sleep(remaining)
        st.markdown(
            '<div class="login-footer"><b>TRANSMISSION COMPANY OF NIGERIA</b><br>'
            'Grid Operations · Secure Access</div>',
            unsafe_allow_html=True,
        )
    return username, password, submitted, verify_result
