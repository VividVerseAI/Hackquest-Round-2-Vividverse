"""
Structured running evidence for testnet evaluation — one JSON object per line.

Three related log families (all via bittensor `bt.logging`; no extra frameworks):

1) Machine-auditable JSON (primary evidence pack)
   Format (grep-friendly):
     [vv_evidence] {"component":"validator|miner","event":"...","trace_key":"r3|v5Gr…abc|m-|-",...}
   Each line includes `trace_key` when correlation fields exist — grep one trace_key to follow
   validator → miner → scoring → weights for Round 2 / correctness demos.

2) Operational single-line (stall / ops dashboards)
     [vv_op] action=finalisation_started round_id=3 phase=evaluation validator_hotkey=5Gr… reason=…
   See `vividverse.utils.ops_log`.

3) Human-readable proof lines (same round_id / trace_key as evidence; quick screenshots)
     [vv_proof] proof=validator_winner_fixed_from_raw_scores round_id=3 trace_key=r3|v…|m…|uid2 winner_uid=2 …
   Emitted at milestone points only (not per-packet). Use `level="debug"` for high-frequency paths.

Example session (illustrative; hotkeys shortened):

  [vv_op] action=evaluation_started round_id=2 phase=evaluation validator_hotkey=5Gr… reason=standalone_collect_submissions
  [vv_evidence] {"component":"validator","event":"query_broadcast_complete","round_id":2,...}
  [vv_proof] proof=validator_broadcast_round_state_ack round_id=2 trace_key=r2|v5Gr…|m-|uid- acknowledged_count=4 ...
  [vv_evidence] {"component":"miner","event":"incoming_query","component":"miner",...}
  [vv_evidence] {"component":"validator","event":"response_collection_complete",...}
  [vv_proof] proof=validator_submissions_collected round_id=2 trace_key=r2|v5Gr…|m-|uid- accepted_valid_submissions=2 ...
  … scoring / winner / weights …
  [vv_evidence] {"component":"validator","event":"winner_determination",...}
  [vv_proof] proof=validator_winner_fixed_from_raw_scores round_id=2 winner_uid=1 winner_hotkey=5FH… ...
  [vv_evidence] {"component":"validator","event":"set_weights",...}
  [vv_proof] proof=validator_set_weights_on_chain_result round_id=2 set_weights_success=true ...
  [vv_proof] proof=validator_finalisation_marker_written round_id=2 mode=standalone ...

UI / operators can reconstruct rounds from [vv_evidence] + [vv_op]; [vv_proof] highlights milestones.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Union

import bittensor as bt
import torch

_COMPONENT = "component"
_EVENT = "event"


def build_trace_key(
    *,
    round_id: Optional[Union[int, str]] = None,
    validator_hotkey: Optional[str] = None,
    miner_hotkey: Optional[str] = None,
    miner_uid: Optional[int] = None,
) -> str:
    """
    Stable join for log correlation across validator and miner processes.
    Format: r<round>|v<validator_ss58>|m<miner_ss58>|uid<n|->
    """
    def short(h: Optional[str]) -> str:
        if not h or h == "-":
            return "-"
        return h if len(h) <= 24 else f"{h[:12]}…{h[-6:]}"

    r = "-" if round_id is None else str(round_id)
    mu = "uid-" if miner_uid is None else f"uid{miner_uid}"
    return f"r{r}|v{short(validator_hotkey)}|m{short(miner_hotkey)}|{mu}"


def _json_safe(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    if isinstance(obj, float):
        return round(obj, 10)
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, torch.Tensor):
        if obj.numel() <= 256:
            return _json_safe(obj.detach().cpu().tolist())
        return {
            "shape": list(obj.shape),
            "dtype": str(obj.dtype),
            "sum": float(obj.sum()),
        }
    return str(obj)


def log_evidence(
    component: str,
    event: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    phase: Optional[str] = None,
    action: Optional[str] = None,
    reason: Optional[str] = None,
    **extra: Any,
) -> None:
    """Emit one structured evidence line (validator / miner activity, scoring, weights, lifecycle)."""
    payload: Dict[str, Any] = {
        _COMPONENT: component,
        _EVENT: event,
    }
    if round_id is not None:
        payload["round_id"] = round_id
    if phase is not None:
        payload["phase"] = phase
    if action is not None:
        payload["action"] = action
    if reason is not None:
        payload["reason"] = reason
    for k, v in extra.items():
        if k not in payload:
            payload[k] = _json_safe(v)
    # Correlate validator ↔ miner ↔ scoring lines via shared trace_key
    if any(
        k in payload and payload[k] is not None
        for k in (
            "round_id",
            "validator_hotkey",
            "miner_hotkey",
            "miner_uid",
            "winner_hotkey",
            "winner_uid",
        )
    ):
        vh = payload.get("validator_hotkey")
        mh = payload.get("miner_hotkey")
        if mh is None and isinstance(payload.get("winner_hotkey"), str):
            mh = payload.get("winner_hotkey")
        mu = payload.get("miner_uid")
        if mu is None and payload.get("winner_uid") is not None:
            try:
                mu = int(payload["winner_uid"])
            except (TypeError, ValueError):
                mu = None
        payload["trace_key"] = build_trace_key(
            round_id=payload.get("round_id"),
            validator_hotkey=vh if isinstance(vh, str) else None,
            miner_hotkey=mh if isinstance(mh, str) else None,
            miner_uid=mu if isinstance(mu, int) else None,
        )
    line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    bt.logging.info(f"[vv_evidence] {line}")


def _fmt_proof_val(v: Any) -> str:
    """Short, single-line safe representation for [vv_proof] key=value tails."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.8g}"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        s = " ".join(v.split())
        if len(s) > 56:
            return f"{s[:24]}…{s[-20:]}"
        return s
    return str(v)[:120]


def log_vv_proof(
    proof: str,
    *,
    level: str = "info",
    round_id: Optional[Union[int, str]] = None,
    validator_hotkey: Optional[str] = None,
    miner_hotkey: Optional[str] = None,
    miner_uid: Optional[int] = None,
    **fields: Any,
) -> None:
    """
    One milestone line for operators and Round 2 evidence packs (pairs with [vv_evidence]).
    Does not replace structured JSON evidence — adds a readable checkpoint with the same trace_key.
    """
    parts: List[str] = [f"[vv_proof] proof={proof}"]
    if round_id is not None:
        parts.append(f"round_id={_fmt_proof_val(round_id)}")
    if validator_hotkey is not None:
        parts.append(f"validator_hotkey={_fmt_proof_val(validator_hotkey)}")
    if miner_hotkey is not None:
        parts.append(f"miner_hotkey={_fmt_proof_val(miner_hotkey)}")
    if miner_uid is not None:
        parts.append(f"miner_uid={miner_uid}")
    if any(
        x is not None
        for x in (round_id, validator_hotkey, miner_hotkey, miner_uid)
    ):
        parts.append(
            "trace_key="
            + build_trace_key(
                round_id=round_id,
                validator_hotkey=validator_hotkey,
                miner_hotkey=miner_hotkey,
                miner_uid=miner_uid,
            )
        )
    for k in sorted(fields.keys()):
        val = fields[k]
        if val is None:
            continue
        parts.append(f"{k}={_fmt_proof_val(val)}")
    line = " ".join(parts)
    lv = (level or "info").lower()
    if lv == "debug":
        bt.logging.debug(line)
    elif lv == "warning":
        bt.logging.warning(line)
    elif lv == "error":
        bt.logging.error(line)
    else:
        bt.logging.info(line)


def summarize_weights_for_evidence(
    weights: torch.Tensor,
    *,
    top_n: int = 16,
    hotkeys: Optional[Sequence[str]] = None,
    max_nonzero_entries: int = 512,
) -> Dict[str, Any]:
    """
    Full nonzero UID→weight mapping (capped), top-N subset with optional hotkeys,
    and normalisation check — for set_weights audit trails.
    """
    w = weights.detach().float().cpu().flatten()
    n = int(w.numel())
    s = float(w.sum().item())
    pairs: List[tuple[int, float]] = [
        (int(i), float(w[i].item())) for i in range(n) if w[i].item() > 0
    ]
    pairs.sort(key=lambda x: -x[1])
    top = pairs[:top_n]

    uid_to_weight: Dict[str, float] = {
        str(u): round(v, 10) for u, v in pairs[:max_nonzero_entries]
    }
    truncated = len(pairs) > max_nonzero_entries

    top_weights: List[Dict[str, Union[int, float, str]]] = []
    for u, v in top:
        row: Dict[str, Union[int, float, str]] = {"uid": u, "weight": round(v, 10)}
        if hotkeys is not None and 0 <= u < len(hotkeys):
            row["hotkey"] = str(hotkeys[u])
        top_weights.append(row)

    out: Dict[str, Any] = {
        "total_uids": n,
        "nonzero_count": len(pairs),
        "weight_sum": round(s, 10),
        "weight_sum_near_one": abs(s - 1.0) < 1e-4 or s == 0.0,
        "top_weights": top_weights,
        "uid_to_weight_nonzero": uid_to_weight,
        "uid_to_weight_truncated": truncated,
    }

    if hotkeys is not None:
        out["nonzero_weights_by_uid_hotkey"] = [
            {
                "uid": u,
                "hotkey": str(hotkeys[u]) if u < len(hotkeys) else "",
                "weight": round(v, 10),
            }
            for u, v in pairs[:max_nonzero_entries]
        ]

    return out


def log_vv_judge(
    criterion: str,
    event: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    **fields: Any,
) -> None:
    """
    Judge-facing evidence summary. One structured JSON line per round per moment.

    grep pattern for judges:
      grep '\\[vv_judge\\]' <logfile>          # all judge lines
      grep 'vv_judge.*round_id.*:3,' <logfile> # round 3 only

    criterion values:
      "C1" = Functional Implementation
      "C2" = Incentive Mechanism Integrity
      "C3" = Proof of Intelligence / Effort
      "C4" = Validator & Scoring Robustness
      "C5" = Conceptual Integrity & Architecture
      "all" = consolidated per-round summary (emitted once per finalisation)
    """
    payload: Dict[str, Any] = {"criterion": criterion, "event": event}
    if round_id is not None:
        payload["round_id"] = round_id
    for k, v in fields.items():
        payload[k] = _json_safe(v)
    line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    bt.logging.info(f"[vv_judge] {line}")


def log_round_in_progress_snapshot(
    *,
    round_id: Union[int, str],
    validator_hotkey: Optional[str] = None,
    phase: str = "evaluation",
    submission_count: Optional[int] = None,
    scored_count: Optional[int] = None,
    critic_score_count: Optional[int] = None,
    evaluation_deadline_unix: Optional[int] = None,
    snapshot_unix: Optional[int] = None,
    **extra: Any,
) -> None:
    """
    Periodic in-progress snapshot fired during the evaluation window.

    Emits both a [vv_judge] line (for grep-based audit) and a [vv_proof] milestone.
    Call this every N steps while phase == "evaluation" and finalisation is not yet due,
    so judges have time-stamped evidence even if the round hasn't closed.

    grep pattern: grep '\\[vv_judge\\].*in_progress_snapshot' <logfile>
    """
    import time as _time
    ts = snapshot_unix if snapshot_unix is not None else int(_time.time())

    log_vv_judge(
        "C1",
        "round_in_progress_snapshot",
        round_id=round_id,
        phase=phase,
        validator_hotkey=validator_hotkey,
        submission_count=submission_count,
        scored_count=scored_count,
        critic_score_count=critic_score_count,
        evaluation_deadline_unix=evaluation_deadline_unix,
        snapshot_unix=ts,
        **extra,
    )


def uid_hotkey_pairs(
    uids: Sequence[int],
    hotkeys: Sequence[str],
) -> List[Dict[str, Union[int, str]]]:
    """Align UID with hotkey for response logs."""
    out: List[Dict[str, Union[int, str]]] = []
    for uid in uids:
        if 0 <= uid < len(hotkeys):
            out.append({"uid": int(uid), "hotkey": str(hotkeys[uid])})
    return out
