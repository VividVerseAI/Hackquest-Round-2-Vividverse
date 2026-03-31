"""
Per-round monotonic lifecycleVersion allocation for validator → platform lifecycle POST.

allocate_lifecycle_emit: read last_committed_version, return (lifecycle_id, next_version).
commit_lifecycle_emit: persist after platform accepted (or no-URL skip).
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional, Tuple

_TABLE = "lifecycle_emit_cursor"


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            round_id INTEGER NOT NULL PRIMARY KEY,
            last_committed_version INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def allocate_lifecycle_emit(
    db_path: Optional[str],
    round_id: Optional[int],
    validator_hotkey: Optional[str],
) -> Tuple[str, int]:
    """
    Next lifecycleVersion for this round (monotonic with commit_lifecycle_emit).
    Same logical push (including HTTP retries) must reuse one allocate + commit pair.
    """
    rid = int(round_id) if round_id is not None else -1
    hk = (validator_hotkey or "").strip() or "unknown"
    if not db_path or not str(db_path).strip():
        digest = hashlib.sha256(f"{hk}|{rid}|1".encode()).hexdigest()
        return f"lv-{digest[:48]}", 1

    with sqlite3.connect(db_path) as conn:
        _ensure_table(conn)
        row = conn.execute(
            f"SELECT last_committed_version FROM {_TABLE} WHERE round_id = ?",
            (rid,),
        ).fetchone()
        last = int(row[0]) if row else 0
        version = last + 1
        digest = hashlib.sha256(f"{hk}|{rid}|{version}".encode()).hexdigest()
        lifecycle_id = f"lv-{digest[:48]}"
        return lifecycle_id, version


def commit_lifecycle_emit(
    db_path: Optional[str],
    round_id: Optional[int],
    committed_version: int,
) -> None:
    """Record successful application of lifecycleVersion for this round."""
    if not db_path or not str(db_path).strip():
        return
    rid = int(round_id) if round_id is not None else -1
    with sqlite3.connect(db_path) as conn:
        _ensure_table(conn)
        conn.execute(
            f"""
            INSERT INTO {_TABLE} (round_id, last_committed_version)
            VALUES (?, ?)
            ON CONFLICT(round_id) DO UPDATE SET
                last_committed_version = MAX(last_committed_version, excluded.last_committed_version)
            """,
            (rid, committed_version),
        )
        conn.commit()
