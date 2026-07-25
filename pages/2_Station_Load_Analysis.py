"""
### FILE: pages/2_Station_Load_Analysis.py
Station-level analysis
"""

import streamlit as st
from utils.auth import login, filter_to_user_region
import plotly.express as px
import pandas as pd
from utils.db import read_feeder_load
from utils.branding import inject_css, page_header, kpi_card, kpi_grid, TCN_COLORS, TCN_RED_SCALE, TCN_CHART_LAYOUT, _style_chart
from datetime import date, timedelta

login()

st.set_page_config(page_title="Station Load Analysis", page_icon="⚡", layout="wide")
inject_css()
page_header("Station Load Analysis", "33kV Feeder Network · Station Drilldown")

today = date.today()
start_default = today - timedelta(days=7)
start_date, end_date = st.date_input("Select date range", value=[start_default, today], key="station_dates")

feeder_df = read_feeder_load(str(start_date), str(end_date))
feeder_df = filter_to_user_region(feeder_df)
if feeder_df.empty:
    st.warning("No data for this range")
    st.stop()

station = st.selectbox("Select Station", options=sorted(feeder_df["station"].dropna().unique()))
station_df = feeder_df[feeder_df["station"] == station]

# station KPIs
grouped_data = station_df.groupby(['reading_date', 'reading_time'])['load_mw'].sum().reset_index()
grouped_data = grouped_data.sort_values(by=['reading_date', 'reading_time'])

max_load_row = grouped_data.loc[grouped_data['load_mw'].idxmax()]
max_load = max_load_row['load_mw']
max_date = max_load_row['reading_date']
max_time = max_load_row['reading_time']

min_load_row = grouped_data.loc[grouped_data['load_mw'].idxmin()]
min_load = min_load_row['load_mw']
min_date = min_load_row['reading_date']
min_time = min_load_row['reading_time']

unique_station = station_df["feeder_33kv"].nunique()

kpi_grid([
    kpi_card("Max Load", f"{max_load:.3f}", "MW", "bolt", "#c81e28"),
    kpi_card("Avg Load", f"{grouped_data['load_mw'].mean():.3f}", "MW", "pulse", "#1e3a7a"),
    kpi_card("Min Load", f"{min_load:.3f}", "MW", "chart", "#1F6C9F"),
    kpi_card("Feeders", f"{unique_station}", "", "building", "#956400"),
])
st.caption(f"Max at {max_date} {max_time} · Min at {min_date} {min_time}")

# plot hourly
station_hourly = station_df.groupby(["reading_time"])["load_mw"].sum().reset_index()
fig = px.line(station_hourly.sort_values("reading_time"), x="reading_time", y="load_mw", title=f"Station hourly load — {station}", color_discrete_sequence=TCN_COLORS)
fig.update_layout(**TCN_CHART_LAYOUT)
_style_chart(fig)
st.plotly_chart(fig, use_container_width=True)

# feeder contributions
feed_contrib = station_df.groupby("feeder_33kv")["load_mw"].mean().reset_index().sort_values("load_mw", ascending=False)
fig2 = px.pie(feed_contrib, names="feeder_33kv", values="load_mw", title="Feeder Contribution (Avg Load)", color_discrete_sequence=TCN_COLORS)
fig2.update_layout(**TCN_CHART_LAYOUT)
st.plotly_chart(fig2, use_container_width=True)