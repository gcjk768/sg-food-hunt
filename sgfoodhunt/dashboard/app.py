"""Streamlit dashboard. Run from the project root:

streamlit run sgfoodhunt/dashboard/app.py -- --config config
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sgfoodhunt.dashboard.data import load_dashboard_data, venue_table

config_dir = "config"
if "--config" in sys.argv:
    config_dir = sys.argv[sys.argv.index("--config") + 1]

st.set_page_config(page_title="SG Food Hunt", layout="wide")
config, run_id, rows, scores = load_dashboard_data(config_dir)
st.title("SG Food Hunt")
if run_id is None:
    st.warning("No scored runs yet. Run `sgfh run` first.")
    st.stop()
st.caption(f"Latest run {run_id} · {len(rows)} venues in the registry")

cats = {c.display_name: c.key for c in config.categories.categories}
with st.sidebar:
    cat_name = st.selectbox("Category", ["(all venues)", *cats])
    cat_key = cats.get(cat_name)
    regions = st.multiselect("Region", ["Central", "East", "West", "North", "North East"])
    budget = st.slider("Max price level ($ to $$$$)", 1, 4, 4)
    halal = st.checkbox("Halal only")
    kids = st.checkbox("Kid friendly only")
    mrt_filter = st.text_input("Nearest MRT contains", "")
    trending = st.checkbox("Trending on social only")
    hide_closed = st.checkbox("Hide closed venues", value=True)

table = venue_table(rows, cat_key, config)
df = pd.DataFrame(table)
if df.empty:
    st.info("Nothing ranked in this category yet.")
    st.stop()
mask = pd.Series(True, index=df.index)
if regions:
    mask &= df["region"].isin(regions)
mask &= df["price"].str.len().fillna(0).le(budget) | df["price"].eq("")
if halal:
    mask &= df["halal"]
if kids:
    mask &= df["kid_friendly"]
if mrt_filter:
    mask &= df["nearest_mrt"].fillna("").str.contains(mrt_filter, case=False)
if trending:
    mask &= df["trending"]
if hide_closed:
    mask &= ~df["status"].str.startswith("CLOSED")
df = df[mask]

st.subheader(f"{len(df)} venues")
show = df.drop(columns=["lat", "lon", "id", "status"])
st.dataframe(
    show,
    column_config={"booking": st.column_config.LinkColumn("Booking / site", display_text="open")},
    hide_index=True,
    use_container_width=True,
)
geo = df.dropna(subset=["lat", "lon"])
if not geo.empty:
    st.subheader("Map")
    st.map(geo[["lat", "lon"]], size=40)
st.subheader("Recent social mentions (last 6 months)")
social = df[df["social_6m"] > 0].sort_values("social_6m", ascending=False)[
    ["name", "social_6m", "trending"]
]
st.dataframe(social, hide_index=True, use_container_width=True) if not social.empty else st.caption(
    "none recorded"
)
