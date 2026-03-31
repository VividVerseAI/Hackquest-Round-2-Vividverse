"""
vividverse/utils/finalisation_marker.py

Persistent finalisation markers for idempotent validator finalisation.

WHY THIS EXISTS:
  The validator can be triggered to finalise a round multiple times (e.g. platform
  cache returning finalisation_due=True repeatedly, or validator restart before
  round state advances). Without a durable marker, we would:
    - Call set_weights multiple times (rate limits, wasted work)
    - Push lifecycle multiple times (platform is resilient but we avoid redundant work)
    - Risk inconsistent state if we crash mid-finalisation and retry.

  This table is the validator's local, durable record: "I have already performed
  finalisation side effects for round N." It survives validator restarts. Before
  any finalisation side effect (set_weights, lifecycle push, canonical chain update),
  we check the marker and exit cleanly if already finalised.

STALE MARKERS VS PLATFORM TRUTH:
  If a marker exists but the platform Round row is still submission/evaluation for
  the same round_id, the marker is stale (e.g. lifecycle push failed after a local
  write, or operator restored DB). The validator removes it when platform phase
  proves the round is still active — see remove_stale_marker_if_platform_phase_active.

MANUAL RECOVERY (already-poisoned vividverse.db):
  When the validator cannot fetch platform state (standalone, offline) but a marker
  blocks progress for a round that should still run, delete the row manually:

    sqlite3 /path/to/vividverse.db \\
      'DELETE FROM finalisation_markers WHERE round_id=<N>;'

  Or interactive sqlite3, then: DELETE FROM finalisation_markers WHERE round_id=<N>; COMMIT;

  Replace <N> with the stuck round_id from logs (grep finalisation_marker).
"""

from __future__ import annotations
import json
import logging
import sqlite3
import time
from typing import Optional

_logger = logging.getLogger(__name__)


def init_db(db_path: str = "vividverse.db") -> None:
    """Create the finalisation_markers table if it does not exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS finalisation_markers (
                round_id INTEGER PRIMARY KEY,
                finalised_at INTEGER NOT NULL
            )
        """)
        # No explicit index needed: round_id INTEGER PRIMARY KEY is itself indexed.
        conn.commit()


def is_round_finalised(round_id: int, db_path: str = "vividverse.db") -> bool:
    """
    Return True if this validator has already finalised the given round.

    Used before any finalisation side effect to skip idempotently.
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM finalisation_markers WHERE round_id = ?",
            (round_id,),
        ).fetchone()
        return row is not None


def clear_finalisation_marker(round_id: int, db_path: str = "vividverse.db") -> None:
    """
    Remove the durable finalisation marker for round_id.

    Used when platform state proves the local marker is stale
    (e.g. platform still shows submission/evaluation for the same round_id).
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM finalisation_markers WHERE round_id = ?",
            (round_id,),
        )
        conn.commit()


def remove_stale_marker_if_platform_phase_active(
    round_id: int,
    platform_round_phase: Optional[str],
    db_path: str = "vividverse.db",
) -> bool:
    """
    If a local marker exists but platform_round_phase is submission or evaluation,
    the marker contradicts committed platform state — remove it.

    Returns True if a row was deleted. Safe to call on every loop; no-op when
    there is no marker or platform phase is finalised / unknown.
    """
    if not is_round_finalised(round_id, db_path):
        return False
    sub = (platform_round_phase or "").lower()
    if sub in ("submission", "evaluation"):
        clear_finalisation_marker(round_id, db_path)
        return True
    return False


def mark_round_finalised(round_id: int, db_path: str = "vividverse.db") -> None:
    """
    Record that this validator has finalised the given round.

    Call only after all finalisation side effects have completed successfully:
    - set_weights when applicable (chain)
    - POST /api/validator/lifecycle with structured result accepted==True (skipped HTTP
      when no PLATFORM_API_URL; still use push_lifecycle_to_platform for the contract)
    - POST /api/validator/lifecycle accepted==True
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO finalisation_markers (round_id, finalised_at) VALUES (?, ?)",
            (round_id, int(time.time())),
        )
        conn.commit()
    _logger.info(
        json.dumps(
            {
                "event": "finalisation_marker_written",
                "round_id": round_id,
                "db_path": db_path,
            }
        )
    )
