#!/usr/bin/env python3
"""
scripts/round_status.py

Print current round and phase — validator cadence view.
Works with or without Platform; use from mechanism folder.

Usage:
    # Platform mode (validator's view — reads from Platform API):
    PLATFORM_API_URL=http://localhost:3000 python scripts/round_status.py

    # Standalone mode (validator's local SQLite):
    python scripts/round_status.py

    # Explicit platform URL:
    python scripts/round_status.py --platform http://localhost:3000
"""
from __future__ import annotations
import os
import sys
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _from_platform(url: str, timeout: int = 20) -> dict | None:
    """Fetch round state from Platform API. Same source validator uses."""
    import requests
    base = (url or "").rstrip("/")
    if base and not base.endswith("/api"):
        base = f"{base}/api"
    try:
        r = requests.get(f"{base}/rounds/current", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def _from_subnet_state(url: str, timeout: int = 20) -> dict | None:
    """Fetch full subnet state (phase, activeRound, etc.)."""
    import requests
    base = (url or "").rstrip("/")
    if base and not base.endswith("/api"):
        base = f"{base}/api"
    try:
        r = requests.get(f"{base}/subnet/state", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def _from_sqlite(db_path: str) -> dict | None:
    """Read round state from validator's local SQLite (standalone mode)."""
    import sqlite3
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT round_id, phase, submission_deadline, evaluation_deadline "
                "FROM round_state WHERE id = 0"
            ).fetchone()
        if row:
            return {
                "roundId": row[0],
                "phase": row[1],
                "submission_deadline_unix": row[2],
                "evaluation_deadline_unix": row[3],
            }
    except Exception:
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show current round and phase (validator cadence). Platform API or local SQLite."
    )
    parser.add_argument(
        "--platform",
        type=str,
        default=os.environ.get("PLATFORM_API_URL"),
        help="Platform URL (e.g. http://localhost:3000). Uses PLATFORM_API_URL if unset.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=os.environ.get("VALIDATOR_DB_PATH", "vividverse.db"),
        help="Validator SQLite path (standalone mode)",
    )
    parser.add_argument("--timeout", type=int, default=20, help="Platform request timeout (seconds)")
    args = parser.parse_args()

    data = None
    source = ""

    if args.platform:
        data = _from_platform(args.platform, args.timeout)
        if data:
            source = "Platform"
        if not data or data.get("roundId") is None:
            subnet = _from_subnet_state(args.platform, args.timeout)
            if subnet:
                ar = subnet.get("activeRound") or {}
                data = {
                    "roundId": ar.get("roundId"),
                    "phase": subnet.get("currentPhase") or ar.get("phase") or subnet.get("filmCycleState") or "—",
                    "filmCycleState": subnet.get("filmCycleState"),
                    "submission_deadline_unix": int(ar.get("submissionDeadlineMs", 0) / 1000) if ar.get("submissionDeadlineMs") else None,
                    "evaluation_deadline_unix": int(ar.get("evaluationDeadlineMs", 0) / 1000) if ar.get("evaluationDeadlineMs") else None,
                    "msRemaining": ar.get("msRemaining"),
                }
                source = "Platform (subnet state)"

    if not data or (data.get("roundId") is None and not data.get("filmCycleState")):
        local = _from_sqlite(args.db)
        if local:
            data = local
            source = "Validator SQLite (standalone)"

    if not data:
        print("Could not fetch round state.")
        if args.platform:
            print(f"  Platform at {args.platform} unreachable or returned no round.")
            print("  Ensure Platform is running with AUTH_MODE=live, HTTP_BRIDGE_URL, Bridge running.")
        else:
            print(f"  No PLATFORM_API_URL set and no round_state in {args.db}")
            print("  Use standalone validator or set PLATFORM_API_URL.")
        return 1

    round_id = data.get("roundId")
    phase = (
        data.get("phase")
        or (data.get("round") or {}).get("phase")
        or data.get("filmCycleState")
        or "—"
    )
    sub_deadline = data.get("submission_deadline_unix") or data.get("submissionDeadline")
    eval_deadline = data.get("evaluation_deadline_unix") or data.get("evaluationDeadline")
    ms_remaining = data.get("msRemaining")

    now = int(time.time())
    sub_remaining = (sub_deadline - now) if isinstance(sub_deadline, (int, float)) else None
    eval_remaining = (eval_deadline - now) if isinstance(eval_deadline, (int, float)) else None

    print(f"Source:  {source}")
    print(f"Round:   {round_id if round_id is not None else '—'}")
    print(f"Phase:   {phase}")
    if ms_remaining is not None and ms_remaining > 0:
        print(f"Remaining: {int(ms_remaining / 1000)}s")
    elif sub_remaining is not None and sub_remaining > 0:
        print(f"Submission deadline: {sub_remaining}s remaining")
    elif eval_remaining is not None and eval_remaining > 0:
        print(f"Evaluation deadline: {eval_remaining}s remaining")
    return 0


if __name__ == "__main__":
    sys.exit(main())
