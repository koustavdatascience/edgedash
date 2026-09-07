"""EdgeDash Dashboard — read-only, rule 38 compliant.

Every data panel reads from the last PASSING cycle only.
The activity log is the sole exception: it shows all cycles
including failed and degraded ones, because the failures are the point.

Run with:
    streamlit run dashboard.py
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from edgedash.config import load_config
from edgedash.storage_factory import get_storage_module, init_db


# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="EdgeDash",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Cached data loaders — short TTL so SQLite isn't hammered on every rerun
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
    config = load_config()
    storage = get_storage_module(config)
    return storage.get_listings(limit, min_score)


@st.cache_data(ttl=30)
def _load_gaps(db_path: str, limit: int = 10) -> list[dict[str, Any]]:
    config = load_config()
    storage = get_storage_module(config)
    return storage.get_latest_snapshot(limit=limit)


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
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # --- Load config + storage ---
    try:
        config = load_config()
        storage = get_storage_module(config)
        init_db(config)
        db_path = config.db_path
    except Exception as exc:
        st.error(f"Configuration error: {exc}")
        return

    stats          = _load_stats(db_path)
    last_passing   = _load_passing_cycle_safe(db_path)
    cycle_log      = _load_cycle_log(db_path, limit=30)
    latest_orch    = _latest_orchestrator_row(cycle_log)

    # -----------------------------------------------------------------------
    # 1. HEADER STRIP
    # -----------------------------------------------------------------------
    st.title("🎯 EdgeDash")

    # Determine whether the most recent cycle is verified
    current_verdict, current_ts = _current_verdict(cycle_log)
    data_ts = last_passing.get("finished_at") if last_passing else None

    if current_verdict in ("failed", "degraded"):
        st.warning(
            f"⚠️ **Latest cycle failed verification** ({_fmt_ts(current_ts)})  \n"
            f"Data below is from the last verified cycle: **{_fmt_ts(data_ts)}**  \n"
            "Stale verified data is shown rather than fresh unverified data (rule 38)."
        )
    elif not last_passing:
        st.info("No verified cycle yet — panels will be empty until the first cycle passes verification.")

    # Metric strip
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Last Verified Cycle", _fmt_ts(data_ts))
    c2.metric("Total Listings", stats.get("total_listings", 0))
    c3.metric("Scored", stats.get("scored_listings", 0))
    c4.metric("Unscored", stats.get("unscored_listings", 0))

    verdict_label = "✅ pass" if current_verdict in ("ok", "complete", "pass", "nothing_to_do") \
                    else ("❌ fail" if current_verdict in ("failed", "fail", "degraded") else "—")
    c5.metric("Current Verdict", verdict_label, delta=_age_str(current_ts), delta_color="off")

    st.divider()

    # -----------------------------------------------------------------------
    # 2. AGENT ACTIVITY LOG
    # -----------------------------------------------------------------------
    st.subheader("Agent Activity Log")
    st.caption("All cycles, including failures and degraded runs. Most recent first.")

    if not cycle_log:
        st.info("No cycles yet. Run `python run_cycle.py` to start.")
    else:
        _render_activity_log(cycle_log)

    st.divider()

    # -----------------------------------------------------------------------
    # 3a. TOP SCORED LISTINGS  +  3b. TOP SKILL GAPS
    # -----------------------------------------------------------------------
    col_listings, col_gaps = st.columns([3, 2])

    with col_listings:
        st.subheader("Top 10 Scored Listings")
        if not last_passing:
            st.caption("Awaiting first verified cycle.")
        else:
            listings = _load_listings(db_path, limit=10, min_score=0)
            _render_listings(listings)

    with col_gaps:
        st.subheader("Top 10 Skill Gaps")
        if not last_passing:
            st.caption("Awaiting first verified cycle.")
        else:
            gaps = _load_gaps(db_path, limit=10)
            _render_gaps(gaps)


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
            f'<div style="background:#ddd;border-radius:4px;height:8px;margin:2px 0 4px">'
            f'<div style="width:{bar_pct}%;background:{bar_col};border-radius:4px;height:8px"></div>'
            f'</div>'
        )

        with st.container(border=True):
            sc, info = st.columns([1, 5])
            sc.markdown(f"**{score}**")
            sc.markdown(score_bar, unsafe_allow_html=True)
            info.markdown(f"**[{title}]({url})**" if url else f"**{title}**")
            info.caption(f"{co}  ·  {reason[:120]}{'…' if len(reason) > 120 else ''}")


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
            f'<div style="background:#ddd;border-radius:4px;height:6px;margin:2px 0 2px">'
            f'<div style="width:{bar_pct}%;background:#e74c3c;border-radius:4px;height:6px"></div>'
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

def _load_passing_cycle_safe(db_path: str) -> dict[str, Any] | None:
    try:
        return _load_last_passing_cycle(db_path)
    except Exception:
        return None


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
