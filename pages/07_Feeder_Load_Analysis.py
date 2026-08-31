"""
### FILE: pages/07_Feeder_Load_Analysis.py
Feeder-level analysis and top feeders
"""

import streamlit as st
from utils.auth import login, filter_to_user_region
import plotly.express as px
import pandas as pd
from utils.db import read_feeder_load
from utils.branding import inject_css, page_header, kpi_card, kpi_grid, TCN_COLORS, TCN_CHART_LAYOUT, _style_chart
from datetime import date, timedelta

login()

st.set_page_config(page_title="Feeder Load Analysis", page_icon="⚡", layout="wide")
inject_css()
page_header("Feeder Load Analysis", "33kV Feeder Network · Feeder Drilldown")

today = date.today()
start_default = today - timedelta(days=7)
start_date, end_date = st.date_input("Select date range", value=[start_default, today], key="feeder_dates")

feeder_df = read_feeder_load(str(start_date), str(end_date))
feeder_df = filter_to_user_region(feeder_df)
if feeder_df.empty:
    st.warning("No data for this range")
    st.stop()

feeder = st.selectbox("Select Feeder", options=sorted(feeder_df["feeder_33kv"].dropna().unique()))
feeder_df_sel = feeder_df[feeder_df["feeder_33kv"] == feeder]

max_idx = feeder_df_sel['load_mw'].idxmax()
max_value = feeder_df_sel.loc[max_idx, 'load_mw']
max_date = feeder_df_sel.loc[max_idx, 'reading_date']
max_time = feeder_df_sel.loc[max_idx, 'reading_time']

kpi_grid([
    kpi_card("Max Load", f"{max_value:.3f}", "MW", "bolt", "#c81e28"),
    kpi_card("Avg Load", f"{feeder_df_sel['load_mw'].mean():.3f}", "MW", "pulse", "#1e3a7a"),
    kpi_card("Min Load", f"{feeder_df_sel['load_mw'].min():.3f}", "MW", "chart", "#1F6C9F"),
])
st.caption(f"Max at {max_date} {max_time}")

# hourly
feeder_hourly = feeder_df_sel.groupby(["reading_time"])["load_mw"].sum().reset_index()
fig = px.line(feeder_hourly.sort_values("reading_time"), x="reading_time", y="load_mw", title=f"Feeder hourly load — {feeder}", color_discrete_sequence=TCN_COLORS)
fig.update_layout(**TCN_CHART_LAYOUT)
_style_chart(fig)
st.plotly_chart(fig, use_container_width=True)