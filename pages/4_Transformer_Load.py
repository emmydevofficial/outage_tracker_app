"""
### FILE: pages/4_Transformer_Load.py
Transformer-level analysis
"""

import streamlit as st
from utils.auth import login, filter_to_user_region
import plotly.express as px
import pandas as pd
from utils.db import read_transformer_load
from utils.branding import inject_css, page_header, kpi_card, kpi_grid, TCN_COLORS, TCN_CHART_LAYOUT, _style_chart
from datetime import date, timedelta

login()

st.set_page_config(page_title="Transformer Load", page_icon="⚡", layout="wide")
inject_css()
page_header("Transformer Load Analysis", "33kV Feeder Network · Transformer Drilldown")

today = date.today()
start_default = today - timedelta(days=7)
start_date, end_date = st.date_input("Select date range", value=[start_default, today], key="transformer_dates")

trans_df = read_transformer_load(str(start_date), str(end_date))
trans_df = filter_to_user_region(trans_df)
if trans_df.empty:
    st.warning("No data for this range")
    st.stop()

station = st.selectbox("Station", options=sorted(trans_df['station'].dropna().unique()))
trans_sel = trans_df[trans_df['station'] == station]

kpi_grid([
    kpi_card("Max Load", f"{trans_sel['load_mw'].max():.3f}", "MW", "bolt", "#c81e28"),
    kpi_card("Avg Load", f"{trans_sel['load_mw'].mean():.3f}", "MW", "pulse", "#1e3a7a"),
])

# calculate max/min for each transformer under selected station
if not trans_sel.empty:
    details = []
    for tx, df_tx in trans_sel.groupby("transformer_nomenclature"):
        max_idx = df_tx['load_mw'].idxmax()
        min_idx = df_tx['load_mw'].idxmin()
        max_row = df_tx.loc[max_idx, ['load_mw','reading_date','reading_time']]
        min_row = df_tx.loc[min_idx, ['load_mw','reading_date','reading_time']]
        # convert times to string to avoid None display
        # always convert to str; this handles Timestamp/NaT or other
        # types and avoids displaying 'None'.
        max_time = str(max_row['reading_time']) if pd.notna(max_row['reading_time']) else ''
        min_time = str(min_row['reading_time']) if pd.notna(min_row['reading_time']) else ''
        details.append([
            tx,
            max_row['load_mw'], max_row['reading_date'], max_time,
            min_row['load_mw'], min_row['reading_date'], min_time,
        ])
    df_tx_ext = pd.DataFrame(details, columns=[
        'transformer', 'max_load', 'max_date', 'max_time',
        'min_load', 'min_date', 'min_time'
    ])
    st.subheader('Per-transformer load extremes')
    st.dataframe(df_tx_ext)

load_by_tx = trans_sel.groupby('transformer_nomenclature')['load_mw'].mean().reset_index().sort_values('load_mw', ascending=False)
fig = px.bar(load_by_tx.head(10), x='transformer_nomenclature', y='load_mw', title=f"Transformer Loading (Avg) — {station}", color_discrete_sequence=TCN_COLORS)
fig.update_layout(**TCN_CHART_LAYOUT)
_style_chart(fig)
st.plotly_chart(fig, use_container_width=True)