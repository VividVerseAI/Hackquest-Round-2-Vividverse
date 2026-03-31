"""
Optional human-readable / JSON snapshot of the last finalised round (scores, winner, weights).

Enabled only via environment (no surprise files):

  VALIDATOR_LAST_ROUND_JSON=1       — write JSON (default path: last_round_summary.json)
  VALIDATOR_LAST_ROUND_JSON_PATH=path/to/file.json  — override output path
  VALIDATOR_PRINT_ROUND_SUMMARY=1   — print a short summary to stdout
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Mapping, Optional

ScoreMap = Mapping[int, float]
WeightMap = Mapping[str, float]


def maybe_dump_last_round_summary(
    *,
    round_id: int,
    mode: str,
    winner_uid: int,
    winner_hotkey: str,
    scores: ScoreMap,
    uid_to_weight_nonzero: Dict[str, float],
    weight_sum: float,
    set_weights_success: bool,
    netuid: int,
    validator_hotkey: str,
    weight_sum_near_one: Optional[bool] = None,
    top_weights_preview: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Emit JSON and/or stdout when env flags are set."""
    do_file = os.environ.get("VALIDATOR_LAST_ROUND_JSON", "").lower() in ("1", "true", "yes")
    do_print = os.environ.get("VALIDATOR_PRINT_ROUND_SUMMARY", "").lower() in ("1", "true", "yes")
    if not do_file and not do_print:
        return

    payload: Dict[str, Any] = {
        "timestamp_unix": int(time.time()),
        "round_id": round_id,
        "mode": mode,
        "netuid": netuid,
        "validator_hotkey": validator_hotkey,
        "winner": {"uid": int(winner_uid), "hotkey": winner_hotkey},
        "raw_scores": {str(k): float(v) for k, v in sorted(scores.items())},
        "weight_distribution_nonzero": dict(uid_to_weight_nonzero),
        "weight_sum": weight_sum,
        "weight_sum_near_one": weight_sum_near_one,
        "top_weights": top_weights_preview or [],
        "set_weights_success": set_weights_success,
    }

    if do_file:
        path = os.environ.get("VALIDATOR_LAST_ROUND_JSON_PATH", "").strip() or "last_round_summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")

    if do_print:
        lines = [
            "",
            "========== last round summary ==========",
            f"round_id={round_id}  mode={mode}  netuid={netuid}  set_weights_success={set_weights_success}",
            f"winner: uid={winner_uid}  hotkey={winner_hotkey}",
            "raw_scores: " + json.dumps(payload["raw_scores"], ensure_ascii=False),
            "weight_sum (nonzero UIDs): " + f"{weight_sum:.10f}  near_one={weight_sum_near_one}",
            "weight_distribution_nonzero: " + json.dumps(payload["weight_distribution_nonzero"], ensure_ascii=False),
            "========================================",
            "",
        ]
        print("\n".join(lines), flush=True)
