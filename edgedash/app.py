"""EdgeDash — "Ask your data" web interface.

Run with:
    streamlit run app.py

Two-call pipeline per rules 42-45: ROUTE (pick a tool) -> PHRASE (prose
from the rows). Every answer shows its underlying rows (rule 44).
"""
from __future__ import annotations

import streamlit as st

from edgedash.query.ask import ask, Answer, _daily_cap_exceeded

EXAMPLES = [
    "Show me the best matches",
    "How many companies are hiring",
    "What skills are in demand for Python",
]


def _format_answer(answer: Answer) -> None:
    st.markdown("### Answer")
    st.write(answer.text)


def _format_rows(rows: list[dict]) -> None:
    """Rule 44 — the data that produced the answer always accompanies it."""
    if not rows:
        st.info("No results found.")
        return
    st.markdown(f"**Showing {len(rows)} result(s):**")
    st.dataframe(rows, hide_index=True)


# Page config — must be the first Streamlit call
st.set_page_config(
    page_title="EdgeDash — Ask Your Data",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("🎯 EdgeDash — Ask Your Data")

config = st.session_state.get("config")
if config is None:
    from edgedash.config import load_config
    config = load_config()
    st.session_state.config = config

daily_cap_exceeded = _daily_cap_exceeded(config)

# ---------------------------------------------------------------------------
# Ask box
# ---------------------------------------------------------------------------

if daily_cap_exceeded:
    st.info(
        f"Daily question limit reached ({config.daily_question_cap} questions). "
        "The ask box is temporarily disabled. Dashboard data is unchanged. "
        "Try again tomorrow."
    )
    st.text_input("Daily question limit reached — ask box disabled:",
                  value="disabled", disabled=True)
else:
    # Three clickable examples — the first thing a visitor does works (rule 45).
    st.markdown("**Try an example:**")
    cols = st.columns(len(EXAMPLES))
    clicked = None
    for col, eq in zip(cols, EXAMPLES):
        if col.button(eq, use_container_width=True):
            clicked = eq

    question = st.text_input(
        "Enter your question:",
        value=clicked or st.session_state.get("last_question", ""),
        placeholder="e.g. Show me the best matches",
    )

    if question:
        st.session_state.last_question = question
        with st.spinner("Routing and executing query..."):
            answer: Answer = ask(question)

        _format_answer(answer)
        _format_rows(answer.rows)

        if answer.tool_used:
            st.caption(f"Answered using the '{answer.tool_used}' tool.")
        else:
            st.caption("No tool matched — the data does not contain this answer.")