"""Independent runup web app. No legacy scanner UI or daemon is started."""
import streamlit as st

from runup.ui.main import render

st.set_page_config(page_title="런업스캐너", page_icon="📅", layout="wide")
render()
