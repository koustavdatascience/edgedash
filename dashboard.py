"""EdgeDash Streamlit entrypoint.

The root route renders the landing page natively in Streamlit. The dashboard
route executes the original verified workspace without embedding React.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent

st.set_page_config(
    page_title="EdgeDash — Autonomous career intelligence",
    page_icon="E",
    layout="wide",
    initial_sidebar_state="collapsed",
)

if st.query_params.get("view") == "dashboard":
    dashboard_path = ROOT / "legacy_dashboard.py"
    exec(
        dashboard_path.read_text(encoding="utf-8"),
        {"__name__": "__main__", "__file__": str(dashboard_path)},
    )
else:
    landing_path = ROOT / "landing_page.py"
    exec(
        landing_path.read_text(encoding="utf-8"),
        {"__name__": "__main__", "__file__": str(landing_path)},
    )
