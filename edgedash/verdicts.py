"""Verification history viewer — read-only, terminal output.

Usage
-----
python -m edgedash.verdicts                  # last 20 cycles
python -m edgedash.verdicts --check score_spread  # filter to a specific failing check
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# ANSI colour helpers — degrade gracefully on terminals that don't support it
# ---------------------------------------------------------------------------

_RED    = "\033[31m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes if stdout is a tty."""
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + _RESET


# ---------------------------------------------------------------------------
# Parsing helpers — extract structured fields from cycle_log notes strings
# ---------------------------------------------------------------------------

def _parse_outcome(notes: str, status: str) -> str:
    """Pull outcome= from orchestrator notes, fall back to status."""
    m = re.search(r"outcome=(\S+?)(?:\s|$|\|)", notes)
    return m.group(1).rstrip("|").strip() if m else status


def _parse_ran(notes: str) -> str:
    """Pull ran=[...] from orchestrator notes."""
    m = re.search(r"ran=\[([^\]]*)\]", notes)
    return m.group(1) if m else ""


def _parse_retries(notes: str) -> int:
    """Pull verification_retries=N from orchestrator notes."""
    m = re.search(r"verification_retries=(\d+)", notes)
    return int(m.group(1)) if m else 0


def _parse_failed_checks(notes: str) -> list[str]:
    """Extract failing check names from a verifier notes string.

    Verifier notes look like:
      VERDICT: fail — score_spread observed spread=4 (threshold min_score_spread=10);
                       freshness observed age=5.2d (threshold max_data_age_days=3)
    """
    checks: list[str] = []
    # Each check segment: "<name> observed ..."
    for m in re.finditer(r"(\w+)\s+observed\s+", notes):
        checks.append(m.group(1))
    return checks


def _parse_verdict_from_notes(notes: str, status: str) -> str:
    """Return 'pass', 'fail', or 'degraded' from any cycle_log row."""
    if "VERDICT: pass" in notes:
        return "pass"
    if "VERDICT: fail" in notes:
        return "fail"
    if status == "degraded" or "degraded" in notes:
        return "degraded"
    # Orchestrator outcome= carries the cycle result
    outcome = _parse_outcome(notes, status)
    if outcome in ("complete", "nothing_to_do"):
        return "pass"
    if outcome in ("partial",):
        return "partial"
    if outcome in ("failed", "degraded"):
        return outcome
    return status


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class CycleRow:
    """One display row, assembled from one or more cycle_log rows."""

    def __init__(self, orch_row: dict[str, Any], verifier_row: dict[str, Any] | None) -> None:
        notes  = orch_row.get("notes", "")
        vstatus = orch_row.get("status", "")

        self.ts            = orch_row.get("finished_at") or orch_row.get("started_at", "—")
        self.ran           = _parse_ran(notes)
        self.outcome       = _parse_outcome(notes, vstatus)
        self.retries       = _parse_retries(notes)

        # Verdict and failed checks come from the verifier row when available
        if verifier_row:
            vnotes         = verifier_row.get("notes", "")
            self.verdict   = _parse_verdict_from_notes(vnotes, verifier_row.get("status", ""))
            self.failed_checks = _parse_failed_checks(vnotes)
        else:
            # No verifier ran this cycle (e.g. nothing was scored)
            self.verdict   = _parse_verdict_from_notes(notes, vstatus)
            self.failed_checks = []

        # Degrade verdict if orchestrator itself is degraded
        if self.outcome == "degraded":
            self.verdict = "degraded"


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_VERDICT_WIDTH = 8   # "degraded" is 8 chars


def _verdict_str(row: CycleRow) -> str:
    v = row.verdict
    if v == "pass":
        return _c(f"{'pass':<{_VERDICT_WIDTH}}", _GREEN, _BOLD)
    if v == "degraded":
        return _c(f"{'degraded':<{_VERDICT_WIDTH}}", _RED, _BOLD)
    if v in ("fail", "failed"):
        return _c(f"{'fail':<{_VERDICT_WIDTH}}", _RED, _BOLD)
    if v == "partial":
        return _c(f"{'partial':<{_VERDICT_WIDTH}}", _YELLOW)
    if v == "nothing_to_do":
        return _c(f"{'skip':<{_VERDICT_WIDTH}}", _GREEN)
    return f"{v:<{_VERDICT_WIDTH}}"


def _fmt_ts(ts: str) -> str:
    """Shorten ISO timestamp to YYYY-MM-DD HH:MM."""
    if not ts or ts == "—":
        return "—"
    # strip subsecond and tz for compactness
    return ts[:16].replace("T", " ")


def _print_header() -> None:
    cols = f"  {'TIMESTAMP':<17}  {'AGENTS RUN':<32}  {'VERDICT':<8}  {'FAILED CHECKS':<38}  {'RETRY'}"
    print(_c(cols, _BOLD))
    print("  " + "─" * (len(cols) - 2))


def _print_row(row: CycleRow) -> None:
    ts_str      = _fmt_ts(row.ts)
    ran_str     = (row.ran or "—")[:32]
    verdict     = _verdict_str(row)
    checks_str  = (", ".join(row.failed_checks) or "—")[:38]
    retry_str   = str(row.retries) if row.retries else "—"

    # Colour failed check names red
    if row.failed_checks:
        checks_str = _c(checks_str, _RED)

    line = f"  {ts_str:<17}  {ran_str:<32}  {verdict}  {checks_str:<38}  {retry_str}"

    # Failed/degraded rows: prefix with a marker
    if row.verdict in ("fail", "failed", "degraded"):
        print(_c("▶ ", _RED) + line)
    else:
        print("  " + line)


def _print_summary(rows: list[CycleRow], check_filter: str | None) -> None:
    print()
    print("─" * 80)

    if not rows:
        print("  No cycles found.")
        return

    evaluable = [r for r in rows if r.verdict not in ("nothing_to_do", "partial", "")]
    passes    = sum(1 for r in evaluable if r.verdict == "pass")
    total_e   = len(evaluable)
    pass_rate = (passes / total_e * 100) if total_e else 0.0

    all_checks: list[str] = []
    for r in rows:
        all_checks.extend(r.failed_checks)

    rate_str = f"{passes}/{total_e} ({pass_rate:.0f}%)"
    print(f"  Pass rate (last {len(rows)}):  {_c(rate_str, _BOLD)}")

    if all_checks:
        counter   = Counter(all_checks)
        top_check, top_count = counter.most_common(1)[0]
        print(f"  Most failing check:   {_c(top_check, _RED, _BOLD)}  ({top_count} time(s))")

        if len(counter) > 1:
            others = ", ".join(f"{n}×{c}" for c, n in counter.most_common()[1:])
            print(f"  Other failing checks: {others}")
    else:
        print("  No failing checks in this window.")

    if check_filter:
        filtered = [r for r in rows if check_filter in r.failed_checks]
        print(f"\n  Cycles where '{check_filter}' failed: {len(filtered)}")


# ---------------------------------------------------------------------------
# Assembly — pair orchestrator rows with their verifier rows
# ---------------------------------------------------------------------------

def _assemble_rows(log: list[dict[str, Any]], limit: int) -> list[CycleRow]:
    """
    cycle_log is ordered newest-first.
    We want the last `limit` ORCHESTRATOR rows (one per cycle), each paired
    with the verifier row that belongs to the same cycle window.
    """
    orch_rows = [r for r in log if r.get("agent") == "orchestrator"][:limit]

    # Build a time-indexed lookup for verifier rows
    verifier_rows = [r for r in log if r.get("agent") == "verifier"]

    rows: list[CycleRow] = []
    for i, orch in enumerate(orch_rows):
        orch_start = orch.get("started_at", "")
        orch_end   = orch.get("finished_at", "")

        # Find the verifier row whose started_at falls between this cycle's
        # started_at and finished_at (simple string comparison works for ISO ts)
        vrow = next(
            (v for v in verifier_rows
             if orch_start <= v.get("started_at", "") <= orch_end),
            None,
        )
        rows.append(CycleRow(orch, vrow))

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show verification history from cycle_log (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m edgedash.verdicts\n"
            "  python -m edgedash.verdicts --check score_spread\n"
            "  python -m edgedash.verdicts --limit 10\n"
        ),
    )
    parser.add_argument(
        "--check",
        metavar="NAME",
        default=None,
        help="Filter to cycles where this specific check failed",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of cycles to show (default: 20)",
    )
    args = parser.parse_args()

    try:
        from edgedash.config import load_config
        from edgedash.storage_factory import get_storage_module, init_db
        config  = load_config()
        storage = get_storage_module(config)
        init_db(config)
    except Exception as exc:
        print(f"Error loading config/storage: {exc}", file=sys.stderr)
        sys.exit(1)

    # Fetch enough raw rows to reliably assemble `limit` orchestrator cycles.
    # Each cycle can have up to ~6 rows (orch + fetcher + scorer + gap + verifier + dist).
    raw_log = storage.get_cycle_log(limit=args.limit * 8)
    rows    = _assemble_rows(raw_log, limit=args.limit)

    if not rows:
        print("No cycles yet.")
        sys.exit(0)

    # Apply --check filter
    if args.check:
        display_rows = [r for r in rows if args.check in r.failed_checks]
        filter_note  = f"  Filtered to cycles where check '{args.check}' failed"
    else:
        display_rows = rows
        filter_note  = None

    # Header
    title = f"Verification History — last {len(rows)} cycle(s)"
    if args.check:
        title += f" · filter: {args.check}"
    print()
    print(_c(f"  {title}", _BOLD))
    print()

    if filter_note:
        print(_c(filter_note, _YELLOW))
        print()

    if not display_rows:
        print(f"  No cycles found where '{args.check}' failed.")
    else:
        _print_header()
        for row in display_rows:
            _print_row(row)

    _print_summary(rows, args.check)
    print()


if __name__ == "__main__":
    main()
