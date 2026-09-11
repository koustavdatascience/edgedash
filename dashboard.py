"""EdgeDash Dashboard — read-only, rule 38 compliant.

Every data panel reads from the last PASSING cycle only.
The activity log is the sole exception: it shows all cycles
including failed and degraded ones, because the failures are the point.

Run with:
    streamlit run dashboard.py

Deployment (rule 50): the page starts and renders even when the database
is empty, unreachable, or mid-migration. It shows a clear status message
instead of a stack trace. Failure detail is logged server-side, never
rendered to a visitor (rule 48).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import streamlit as st

from edgedash.config import load_config
from edgedash.storage_factory import get_storage_module, init_db
from edgedash.verified import (
    last_passing_cycle,
    verified_listings as _v_listings,
    latest_snapshot_as_of as _v_snapshots,
)

logger = logging.getLogger("edgedash.dashboard")

REPO_URL = "https://github.com/koustavdatascience/edgedash"


# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="EdgeDash",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def _inject_ui_theme() -> None:
    """Apply the EdgeDash visual system consistently across Streamlit widgets."""
    st.markdown(
        """
        <style>
        :root { --ed-blue:#60a5fa; --ed-cyan:#22d3ee; --ed-ink:#0b1220; --ed-muted:#94a3b8; }
        .stApp { background: radial-gradient(circle at 8% 0%, #172554 0, #0b1220 34%, #070b14 100%); }
        [data-testid="stHeader"] { background: transparent; }
        .block-container { max-width: 1440px; padding: 2.5rem 3.5rem 3rem; }
        h1, h2, h3 { letter-spacing: -0.025em; }
        h2 { margin-top: .35rem; }
        [data-testid="stMetric"] { background: rgba(15, 23, 42, .72); border: 1px solid rgba(148,163,184,.16); border-radius: 16px; padding: 1rem 1.15rem; box-shadow: 0 10px 28px rgba(0,0,0,.16); }
        [data-testid="stMetricLabel"] { color: #94a3b8; font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; }
        [data-testid="stMetricValue"] { color: #f8fafc; font-weight: 750; }
        [data-testid="stVerticalBlockBorderWrapper"] { border-color: rgba(148,163,184,.18); background: rgba(15,23,42,.38); border-radius: 18px; }
        .ed-hero { display:flex; align-items:flex-end; justify-content:space-between; gap:2rem; padding:1.5rem 1.7rem; margin-bottom:1.4rem; border:1px solid rgba(96,165,250,.22); border-radius:22px; background:linear-gradient(115deg, rgba(30,64,175,.45), rgba(15,23,42,.35) 62%, rgba(8,47,73,.35)); box-shadow:0 18px 55px rgba(2,6,23,.32); }
        .ed-kicker { color:#67e8f9; font-size:.72rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; margin-bottom:.45rem; }
        .ed-title { color:#f8fafc; font-size:2.35rem; font-weight:800; line-height:1.05; margin:0; }
        .ed-subtitle { color:#cbd5e1; margin:.55rem 0 0; font-size:.98rem; }
        .ed-chip { color:#bfdbfe; border:1px solid rgba(147,197,253,.28); border-radius:999px; padding:.5rem .8rem; white-space:nowrap; font-size:.82rem; background:rgba(30,64,175,.22); }
        .ed-section-note { color:#94a3b8; font-size:.86rem; margin-top:-.35rem; margin-bottom:.8rem; }
        .stButton > button { border-radius:10px; border:1px solid rgba(96,165,250,.24); background:rgba(30,41,59,.72); color:#dbeafe; }
        .stButton > button:hover { border-color:#60a5fa; color:#fff; background:rgba(37,99,235,.28); }
        div[data-baseweb="input"] { border-radius:12px; }
        footer { visibility:hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_ui_theme()


# ---------------------------------------------------------------------------
# Cached data loaders — short TTL so the database isn't hammered on every rerun
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _load_stats(db_path: str) -> dict[str, Any]:
    config = load_config()
    storage = get_storage_module(config)
    return storage.get_stats()


@st.cache_data(ttl=30)
def _load_last_passing_cycle(db_path: str) -> dict[str, Any] | None:
    config = load_config()
    storage = get_storage_module(config)
    return storage.get_last_passing_cycle()


@st.cache_data(ttl=30)
def _load_cycle_log(db_path: str, limit: int = 30) -> list[dict[str, Any]]:
    config = load_config()
    storage = get_storage_module(config)
    return storage.get_cycle_log(limit)


@st.cache_data(ttl=30)
def _load_listings(db_path: str, limit: int, min_score: int) -> list[dict[str, Any]]:
    """Return scored listings from the last passing cycle only (rule 38)."""
    config = load_config()
    storage = get_storage_module(config)
    cycle = last_passing_cycle(storage)
    if cycle is None:
        return []
    all_verified = _v_listings(storage, cycle, limit=5000)
    scored = [r for r in all_verified if r.get("fit_score") is not None and r["fit_score"] >= min_score]
    scored.sort(key=lambda r: (-int(r.get("fit_score") or 0), str(r.get("title") or "").lower()))
    return scored[:limit]


@st.cache_data(ttl=30)
def _load_gaps(db_path: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return skill gaps from the last passing cycle only (rule 38)."""
    config = load_config()
    storage = get_storage_module(config)
    cycle = last_passing_cycle(storage)
    if cycle is None:
        return []
    return _v_snapshots(storage, cycle, limit=limit)


def _load_health_report(db_path: str, backend_key: str) -> dict[str, str]:
    """Return health status message safely (rule 50 compliant)."""
    try:
        config = load_config()
        storage = get_storage_module(config)
        from edgedash.health import evaluate_health
        report = evaluate_health(storage)
        return {
            "status": report.dashboard_status,
            "message": report.dashboard_message,
        }
    except Exception:
        logger.exception("dashboard: health report failed")
        return {
            "status": "amber",
            "message": "🟡 Stale: Health status unavailable",
        }



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_ts(ts: str | None) -> str:
    """ISO timestamp → human-readable local string, or 'never'."""
    if not ts:
        return "never"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return ts


def _age_str(ts: str | None) -> str:
    """Return '2h ago', '3d ago', etc."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = (datetime.now(timezone.utc) - dt).total_seconds()
        if secs < 120:
            return "just now"
        if secs < 3600:
            return f"{int(secs // 60)}m ago"
        if secs < 86_400:
            return f"{int(secs // 3600)}h ago"
        return f"{int(secs // 86_400)}d ago"
    except ValueError:
        return ""


def _next_run_datetime(config: Any) -> str | None:
    """Best-effort next scheduled run time based on config.schedule_interval.

    No scheduler runs in the dashboard process (rule 49) — this is purely an
    estimate of when the scheduled job will next run.
    """
    interval = getattr(config, "schedule_interval", "hourly")
    now = datetime.now(timezone.utc)
    try:
        if interval == "hourly":
            nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        elif interval == "daily":
            nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif interval == "daily_6am":
            nxt = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
        elif interval == "weekly":
            nxt = (now + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif interval.startswith("every_"):
            parts = interval.split("_")
            value, unit = int(parts[1]), parts[2]
            if unit == "minutes":
                nxt = now + timedelta(minutes=value)
            elif unit == "hours":
                nxt = now + timedelta(hours=value)
            else:
                return None
        else:
            return None
        return nxt.isoformat()
    except Exception:
        return None


def _parse_cycle_notes(notes: str) -> dict[str, str]:
    """Extract key=value pairs from orchestrator summary notes string."""
    result: dict[str, str] = {}
    for match in re.finditer(r"(\w+)=(\[[^\]]*\]|[^\s|]+)", notes):
        result[match.group(1)] = match.group(2)
    return result


def _verdict_from_log(row: dict[str, Any]) -> str:
    """Extract VERDICT from a verifier cycle_log notes string."""
    notes = row.get("notes", "")
    if "VERDICT: pass" in notes:
        return "pass"
    if "VERDICT: fail" in notes:
        return "fail"
    return row.get("status", "")


def _row_style(status: str) -> str:
    if status in ("failed", "fail", "degraded"):
        return "🔴"
    if status in ("partial",):
        return "🟡"
    if status in ("ok", "complete", "pass", "nothing_to_do"):
        return "🟢"
    return "⚪"


# ---------------------------------------------------------------------------
# Hostile-startup guards (rule 50)
# ---------------------------------------------------------------------------

def _load_streamlit_secrets() -> None:
    """Bridge Streamlit Cloud secrets to the app's normal environment contract."""
    for key in ("DATABASE_URL", "GEMINI_API_KEY", "EDGEDASH_ENV"):
        if os.environ.get(key):
            continue
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value:
            os.environ[key] = str(value)


def _db_status(config: Any) -> str:
    """Return 'ok' | 'missing' | 'unreachable' for the active deploy.

    Local dev (EDGEDASH_ENV != production) falls back to SQLite and is always
    'ok'. In production the hosted database is required (rule 47).
    """
    if os.environ.get("EDGEDASH_ENV", "dev") != "production":
        return "ok"
    if not os.environ.get("DATABASE_URL"):
        return "missing"
    # Presence of a URL means configuration is available; _init_storage then
    # performs the real connection and converts any failure to "unreachable".
    return "ok"


def _init_storage() -> tuple[str, Any, Any | None]:
    """(status, config, storage) — never raises. Detail is logged server-side."""
    _load_streamlit_secrets()
    try:
        config = load_config()
    except Exception:
        logger.exception("dashboard: config load failed")
        return "config", None, None

    status = _db_status(config)
    if status != "ok":
        return status, config, None

    try:
        storage = get_storage_module(config)
        init_db(config)
        return "ok", config, storage
    except Exception:
        logger.exception("dashboard: database init/connect failed")
        # If the URL was explicitly requested, call it unreachable; otherwise
        # local fallback is presumed fine (storage logged its own state).
        return ("unreachable" if os.environ.get("DATABASE_URL") else "ok"), config, None


def _render_db_status(status: str, config: Any | None) -> None:
    """Static status page — a stranger never sees a traceback (rule 50)."""
    st.title("🎯 EdgeDash")

    if status == "missing":
        st.error("**Database not configured**")
        st.markdown(
            "This dashboard reads from a hosted database, and none is configured "
            "yet. Set **`DATABASE_URL`** (and **`EDGEDASH_ENV=production`**) in the "
            "deployment secrets, then restart the app.  \n"
            "The page will come alive automatically once the database is reachable."
        )
    elif status == "unreachable":
        st.error("**Database unreachable**")
        st.markdown(
            "The database could not be reached. It may be migrating, restarting, "
            "or briefly unavailable. This is a temporary state — the page will "
            "load automatically when the database is reachable again.  \n"
            "The technical detail was recorded in the server log."
        )
    else:
        st.error("**Could not start**")
        st.markdown(
            "The dashboard could not load its configuration. The detail was "
            "recorded in the server log."
        )

    _render_footer(None)


# ---------------------------------------------------------------------------
# Panel wrapper — one failing panel cannot take down the page (rule 50)
# ---------------------------------------------------------------------------

def _panel(key: str, render_fn: Callable[[], None]) -> None:
    try:
        render_fn()
    except Exception:
        logger.exception("dashboard: panel failed (%s)", key)
        st.error("This panel failed to load. The detail was logged server-side.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    status, config, storage = _init_storage()
    if status != "ok" or config is None:
        _render_db_status(status, config)
        return

    db_path = config.db_path

    # -----------------------------------------------------------------------
    # 0. HEADER STRIP
    # -----------------------------------------------------------------------
    st.title("🎯 EdgeDash")
    _panel("header", lambda: _render_header(db_path))
    st.divider()

    # -----------------------------------------------------------------------
    # 1. AGENT ACTIVITY LOG
    # -----------------------------------------------------------------------
    _panel("activity_log", lambda: _render_activity_panel(db_path, config))

    st.divider()

    # -----------------------------------------------------------------------
    # 2. TOP SCORED LISTINGS  +  3. TOP SKILL GAPS
    # -----------------------------------------------------------------------
    col_listings, col_gaps = st.columns([3, 2])
    with col_listings:
        _panel("listings", lambda: _render_listings_panel(db_path, config))
    with col_gaps:
        _panel("gaps", lambda: _render_gaps_panel(db_path, config))

    st.divider()

    # -----------------------------------------------------------------------
    # 4. ASK YOUR DATA — rules 42-45
    # -----------------------------------------------------------------------
    _panel("ask", lambda: _render_ask_section(config))

    _render_footer(db_path)


def _render_header(db_path: str) -> None:
    backend_key = "postgres" if os.environ.get("DATABASE_URL") else "sqlite"
    health = _load_health_report(db_path, backend_key)
    st.markdown(
        """
        <div class="ed-hero">
          <div>
            <div class="ed-kicker">Autonomous opportunity intelligence</div>
            <div class="ed-title">EdgeDash</div>
            <div class="ed-subtitle">A verified view of the latest listings, fit signals, and skill gaps.</div>
          </div>
          <div class="ed-chip">Live workspace · Supabase</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(health.get("message", "⚪ Health status unavailable"))

    last_passing = _load_last_passing_cycle(db_path)
    cycle_log = _load_cycle_log(db_path, limit=30)
    stats = _load_stats(db_path)

    current_verdict, current_ts = _current_verdict(cycle_log)
    data_ts = last_passing.get("finished_at") if last_passing else None

    if current_verdict in ("failed", "degraded"):
        st.warning(
            f"⚠️ **Latest cycle failed verification** ({_fmt_ts(current_ts)})  \n"
            f"Data below is from the last verified cycle: **{_fmt_ts(data_ts)}**  \n"
            "Stale verified data is shown rather than fresh unverified data (rule 38)."
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Last Verified Cycle", _fmt_ts(data_ts))
    c2.metric("Total Listings", stats.get("total_listings", 0))
    c3.metric("Scored", stats.get("scored_listings", 0))
    c4.metric("Unscored", stats.get("unscored_listings", 0))

    verdict_label = "✅ pass" if current_verdict in ("ok", "complete", "pass", "nothing_to_do") \
                    else ("❌ fail" if current_verdict in ("failed", "fail", "degraded") else "—")
    c5.metric("Current Verdict", verdict_label, delta=_age_str(current_ts), delta_color="off")


def _render_activity_panel(db_path: str, config: Any) -> None:
    st.subheader("Agent Activity Log")
    st.markdown('<div class="ed-section-note">Every run, including failures and degraded cycles — newest first.</div>', unsafe_allow_html=True)

    cycle_log = _load_cycle_log(db_path, limit=30)
    if not cycle_log:
        nxt = _next_run_datetime(config)
        when = f"**{_fmt_ts(nxt)}**" if nxt else "as scheduled"
        st.info(
            f"No cycles yet — the first run is scheduled for {when}. "
            "The dashboard will populate automatically after it completes."
        )
        return
    _render_activity_log(cycle_log)


def _render_listings_panel(db_path: str, config: Any) -> None:
    st.subheader("Top 10 Scored Listings")
    st.markdown('<div class="ed-section-note">Highest-fit opportunities from the latest verified cycle.</div>', unsafe_allow_html=True)
    last_passing = _load_last_passing_cycle(db_path)
    if not last_passing:
        _empty_state_caption(config)
        return
    _render_listings(_load_listings(db_path, limit=10, min_score=0))


def _render_gaps_panel(db_path: str, config: Any) -> None:
    st.subheader("Top 10 Skill Gaps")
    st.markdown('<div class="ed-section-note">Skills creating the largest opportunity cost.</div>', unsafe_allow_html=True)
    last_passing = _load_last_passing_cycle(db_path)
    if not last_passing:
        _empty_state_caption(config)
        return
    _render_gaps(_load_gaps(db_path, limit=10))


def _empty_state_caption(config: Any) -> None:
    nxt = _next_run_datetime(config)
    when = f"**{_fmt_ts(nxt)}**" if nxt else "as scheduled"
    st.caption(f"No data yet — the first scheduled run is {when}.")


_ASK_EXAMPLES = [
    "Show me the best matches",
    "How many companies are hiring",
    "What skills are in demand for Python",
]


def _render_ask_section(config: Any) -> None:
    """Ask-your-data box — rules 42-45, integrated into the dashboard.

    The model routes once and phrases once (rule 42). Every answer shows the
    rows that produced it (rule 44). Refusals list what CAN be asked (rule 45).
    """
    from edgedash.query.ask import ask, Answer, _daily_cap_exceeded

    st.subheader("💬 Ask Your Data")
    st.markdown('<div class="ed-section-note">Ask in plain English. Answers use only the last verified cycle and show the supporting rows.</div>', unsafe_allow_html=True)

    if _daily_cap_exceeded(config):
        cap = getattr(config, "daily_question_cap", 200)
        st.info(
            f"Daily question limit reached ({cap} questions). "
            "The ask box is temporarily disabled. Dashboard data is unchanged."
        )
        return

    clicked = None
    cols = st.columns(len(_ASK_EXAMPLES))
    for col, example in zip(cols, _ASK_EXAMPLES):
        if col.button(example, use_container_width=True):
            clicked = example

    question = st.text_input(
        "Enter your question:",
        value=clicked or st.session_state.get("last_question", ""),
        placeholder="e.g. Show me the best matches",
    )

    if not question:
        return

    st.session_state.last_question = question
    with st.spinner("Routing and executing query..."):
        try:
            answer: Answer = ask(question)
        except Exception:
            logger.exception("dashboard: ask question failed")
            st.error(
                "The system could not answer that question. "
                "The detail was recorded in the server log."
            )
            return

    st.markdown("### Answer")
    st.markdown(answer.text)

    if not answer.rows:
        st.info("No results found.")
    else:
        st.markdown(f"**Showing {len(answer.rows)} result(s):**")
        st.dataframe(answer.rows, hide_index=True)

    if answer.tool_used:
        st.caption(f"Answered using the '{answer.tool_used}' tool.")
    else:
        st.caption("No tool matched — the data does not contain this answer.")


def _render_footer(db_path: str | None) -> None:
    st.divider()
    fcol1, fcol2 = st.columns([3, 1])
    with fcol1:
        if db_path:
            last_passing = None
            try:
                last_passing = _load_last_passing_cycle(db_path)
            except Exception:
                pass
            ts = last_passing.get("finished_at") if last_passing else None
            label = f"Last verified cycle: **{_fmt_ts(ts)}**" if ts else "Last verified cycle: **never**"
            st.caption(label)
    with fcol2:
        st.markdown(f"[📦 Source on GitHub]({REPO_URL})")


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_activity_log(rows: list[dict[str, Any]]) -> None:
    """Render the agent activity log as a styled table."""

    # Aggregate orchestrator summary rows into display rows
    # For non-orchestrator rows (per-agent), show them inline under their cycle
    orch_rows  = [r for r in rows if r.get("agent") == "orchestrator"]
    other_rows = [r for r in rows if r.get("agent") != "orchestrator"]

    # If no orchestrator rows yet, show raw log
    display_rows = orch_rows if orch_rows else rows

    for row in display_rows:
        notes   = row.get("notes", "")
        status  = row.get("status", "")
        agent   = row.get("agent", "")
        ts      = row.get("finished_at") or row.get("started_at", "")
        icon    = _row_style(status)

        # Parse structured notes for orchestrator rows
        parsed  = _parse_cycle_notes(notes)
        outcome = parsed.get("outcome", status)
        ran     = parsed.get("ran", "")
        skipped = parsed.get("skipped", "")
        retries = parsed.get("verification_retries", "0")

        # Extract verdict and failed check from notes
        verdict_str = ""
        if "VERDICT: pass" in notes:
            verdict_str = "✅ pass"
        elif "VERDICT: fail" in notes:
            # pull the check name out
            m = re.search(r"VERDICT: fail — (\S+)", notes)
            verdict_str = f"❌ {m.group(1)}" if m else "❌ fail"
        elif outcome == "degraded":
            verdict_str = "💀 degraded"
        elif outcome in ("complete", "nothing_to_do"):
            verdict_str = "✅ pass" if not retries or retries == "0" else f"✅ pass (retry {retries})"

        # Duration from notes
        dur_match = re.search(r"duration=\[([^\]]+)\]", notes)
        duration_str = dur_match.group(1) if dur_match else "—"

        # Visual distinction: failed/degraded get a colored container
        if outcome in ("failed", "degraded") or status in ("failed", "degraded"):
            container = st.container(border=True)
        else:
            container = st.container()

        with container:
            hcol1, hcol2, hcol3, hcol4 = st.columns([2, 3, 2, 2])
            hcol1.markdown(f"{icon} **{_fmt_ts(ts)}**")
            hcol2.markdown(f"ran: `{ran or '—'}`")
            hcol3.markdown(f"verdict: {verdict_str or '—'}")
            hcol4.markdown(f"⏱ {duration_str}")

            if skipped and skipped != "[]":
                st.caption(f"skipped: {skipped}")
            if outcome in ("failed", "degraded"):
                # Show the failure detail
                fail_detail = re.sub(r"outcome=\S+\s*\|?\s*", "", notes).strip(" |")
                st.caption(f"⚠️ {fail_detail}")


def _render_listings(listings: list[dict[str, Any]]) -> None:
    if not listings:
        st.caption("No scored listings yet.")
        return

    for lst in listings:
        score  = lst.get("fit_score", 0)
        title  = lst.get("title", "—")
        co     = lst.get("company", "—")
        reason = lst.get("fit_reason", "")
        url    = lst.get("url", "")

        bar_pct = min(max(int(score), 0), 100)
        bar_col = "#2ecc71" if score >= 70 else ("#f39c12" if score >= 50 else "#e74c3c")

        score_bar = (
            f'<div style="background:#1e293b;border-radius:999px;height:7px;margin:6px 0 4px">'
            f'<div style="width:{bar_pct}%;background:{bar_col};border-radius:999px;height:7px"></div>'
            f'</div>'
        )

        with st.container(border=True):
            sc, info = st.columns([1, 5])
            sc.markdown(f'<div style="font-size:1.35rem;font-weight:800;color:{bar_col}">{score}</div><div style="color:#94a3b8;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em">fit</div>', unsafe_allow_html=True)
            sc.markdown(score_bar, unsafe_allow_html=True)
            info.markdown(f"**[{title}]({url})**" if url else f"**{title}**")
            info.caption(f"{co}  ·  {reason[:140]}{'…' if len(reason) > 140 else ''}")


def _render_gaps(gaps: list[dict[str, Any]]) -> None:
    if not gaps:
        st.caption("No gap data yet.")
        return

    max_cost = max((g.get("opportunity_cost", 0) for g in gaps), default=1) or 1

    for gap in gaps:
        skill   = gap.get("skill", "?")
        cost    = gap.get("opportunity_cost", 0)
        n       = gap.get("listings_blocked", 0)
        low_conf = gap.get("low_confidence", False)
        bar_pct = int(cost / max_cost * 100)

        bar_html = (
            f'<div style="background:#1e293b;border-radius:999px;height:6px;margin:5px 0 3px">'
            f'<div style="width:{bar_pct}%;background:linear-gradient(90deg,#fb7185,#f97316);border-radius:999px;height:6px"></div>'
            f'</div>'
        )
        label = f"{'⚠ ' if low_conf else ''}**{skill}**"
        caption = f"{n} listing{'s' if n != 1 else ''} · cost {cost:.1f}"
        if low_conf:
            caption += " · low confidence"

        st.markdown(label)
        st.markdown(bar_html, unsafe_allow_html=True)
        st.caption(caption)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _latest_orchestrator_row(log: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in log:
        if row.get("agent") == "orchestrator":
            return row
    return None


def _current_verdict(log: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Return (verdict_status, timestamp) from the most recent orchestrator row."""
    row = _latest_orchestrator_row(log)
    if not row:
        return "", None
    status = row.get("status", "")
    ts     = row.get("finished_at") or row.get("started_at")
    return status, ts


# ---------------------------------------------------------------------------
# Entry point — Streamlit executes the file top-to-bottom; call main() here.
# ---------------------------------------------------------------------------

main()
