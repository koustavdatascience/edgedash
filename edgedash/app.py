"""EdgeDash — "Ask your data" web interface.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import streamlit as st
from typing import Any

from edgedash.query.ask import ask, Answer, _daily_cap_exceeded, _rate_limit_exceeded


# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="EdgeDash — Ask Your Data",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_answer(answer: Answer) -> str:
    """Display the answer text."""
    st.markdown(f"### Answer")
    st.write(answer.text)


def _format_rows(rows: list[dict[str, Any]]) -> None:
    """Display the result rows in a table."""
    if not rows:
        st.info("No results found.")
        return
    st.markdown(f"**Showing {len(rows)} result(s):**")
    st.dataframe(rows, hide_index=True)


# ---------------------------------------------------------------------------
# Example question buttons
# ---------------------------------------------------------------------------

st.sidebar.title("Example Questions")
st.sidebar.caption("Click any example to run it as a query:")

example_questions = [
    "Show me the best matches",
    "How many companies are hiring",
    "What skills are in demand for Python",
]

selected_question = None
for i, eq in enumerate(example_questions):
    if st.sidebar.button(eq, key=f"example_{i}"):
        selected_question = eq

# ---------------------------------------------------------------------------
# Daily‑cap check (global, config‑driven)
# ---------------------------------------------------------------------------
config = st.session_state.get("config", None)
if config is None:
    from edgedash.config import load_config
    config = load_config()
    st.session_state.config = config

daily_cap_exceeded = _daily_cap_exceeded(config)

# ---------------------------------------------------------------------------
# Main: text input + answer (ask box disabled if daily cap exceeded)
# ---------------------------------------------------------------------------

st.title("🎯 EdgeDash — Ask Your Data")

# If daily cap is exceeded, show dashboard normally but disable ask box
if daily_cap_exceeded:
    st.info(
        f"Daily question limit reached ({config.daily_question_cap or 200} questions). "
        "The ask box is temporarily disabled. Dashboard data is unchanged. "
        "Try again tomorrow."
    )
    st.text_input(
        "Daily question limit reached — ask box disabled:",
        value="disabled",
        disabled=True,
    )
    st.caption("The dashboard panels keep working — only the ask box degrades.")
else:
    st.info("Enter a question about the job market below or select an example:")

    question = st.text_input(
        "Enter your question:",
        value=selected_question or "",
        placeholder="e.g. Show me the best matches",
    )

    if question:
        with st.spinner("Routing and executing query..."):
            answer: Answer = ask(question)

        # Display answer
        _format_answer(answer)

        # Display rows
        _format_rows(answer.rows)

        # Brief status line
        if answer.tool_used:
            st.caption(f"Query answered using '{answer.tool_used}' tool")
        else:
            st.caption("No tool matched your question — all available data shown above.")


# ---------------------------------------------------------------------------
# Example question handlers (run ask under the hood for demos)
# ---------------------------------------------------------------------------

st.sidebar.title("Example Questions")
st.sidebar.caption("Click any example to run it as a query:")

example_questions = [
    "Show me the best matches",
    "How many companies are hiring",
    "What skills are in demand for Python",
]

for i, eq in enumerate(example_questions):
    if st.sidebar.button(eq, key=f"example_{i}"):
        # Run ask directly; if daily cap exceeded, the ask function will
        # return the "cap exceeded" Answer (but we already checked above,
        # so this should not happen unless the cap was hit between renders).
        with st.spinner("Running example query..."):
            answer: Answer = ask(eq)
        _format_answer(answer)
        _format_rows(answer.rows)
        st.caption(f"Example '{eq}' processed using '{answer.tool_used or 'no tool'}' mode")


# ---------------------------------------------------------------------------
# Rule 44 / 45 compliance notes
# ---------------------------------------------------------------------------
with st.expander("How this works (rules 42–45)"):
    st.markdown(
        """
        **ROUTE** (one `llm.complete_json` call): the model selects a tool from
        the registry or returns `null`. The prompt lists all 7 tools with their
        parameter schemas; the model must pick exactly one or `null`.

        **EXECUTE**: the selected tool runs with clamped parameters. No `eval`,
        no `getattr` on model-supplied strings.

        **PHRASE** (one `llm.complete_json` call): the model writes 2–3 sentences
        using *only* numbers from the returned rows. If rows are empty, it states
        the data does not contain an answer. The tool's `summary` is included so
        the user knows what was searched.

        **NULL TOOL**: if the model returns `null`, a fixed message lists all
        available tools in plain English. No phrasing call is made.

        **ABUSE GUARDS** (rules for public URL):
          - **Daily cap** (config, default 200/day): if exceeded, the ask box
            disables gracefully; dashboard data panels keep working.
          - **Rate limit** (10 questions / 10 min per session): after exceed,
            a friendly wait‑time message is shown; no model call is made.
          - **Input guards** (before any model call):
            * 300‑char maximum
            * empty/whitespace rejection
            * control‑character stripping
            * injection‑pattern detection ("ignore previous", "system prompt",
              "you are now") → immediate can‑answer response, logged as suspicious
          - **Logging**: every rejection, cap exceed, and successful query is
            written to `query_log` with reason, so you can see what people
            actually send.

        Only the ask box degrades under any condition. The dashboard's data
        panels (top scored listings, skill gaps, agent activity log, cycle
        history, statistics) are completely unaffected.
        """
    )