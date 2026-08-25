# PROJECT: Streamlit + Plotly Dashboard for Transmission Loads & Outages
# File layout (multiple files concatenated below with clear separators)

"""
### FILE: app.py
Entry point for the Streamlit app. This file provides a home/landing page and links to pages.
Run with: streamlit run app.py
"""

import streamlit as st
from utils.auth import login
from utils.branding import inject_css, page_header, credits_section

# require login before doing anything else
login()

st.set_page_config(page_title="Power Ops Dashboard", page_icon="⚡", layout="wide")
inject_css()
page_header("Load & Outage Analytics", "33kV Feeder Network · Load · Outages · Analytics")

st.markdown(
    """
    This Streamlit app contains multiple pages (use the left sidebar Pages menu).

    Pages included:
    1. Region Load Analysis
    2. Station Load Analysis
    3. Feeder Load Analysis
    4. Transformer Load
    5. Outage Analytics
    6. Reliability KPI Report
    7. Regional Dashboard
    8. Upload Feeder Load (33kV feeder hourly load tracking upload)
    9. Upload Line Load (330/132kV line hourly load tracking upload)
    10. Upload Transformer Load (transformer hourly load tracking upload)
    11. User Management (Super Admin only)
    12. Load Data Management (delete feeder/line/transformer load by date and region)
    13. Activity Log (Super Admin only: audit trail + uploaded files directory)

    The pages live in the `pages/` folder. Make sure you have a `utils/` folder with
    `db.py` and `pdf_generator.py`.
    """
)

st.sidebar.header("Quick actions")
if st.sidebar.button("Refresh data cache"):
    st.rerun()

#credits_section()


# ------------------------------------------------------------------
# Separator for next files
# ------------------------------------------------------------------



# ------------------------------------------------------------------
# Separator for next file
# ------------------------------------------------------------------




# ------------------------------------------------------------------
# Separator for next files (pages)
# ------------------------------------------------------------------












# End of concatenated project files
