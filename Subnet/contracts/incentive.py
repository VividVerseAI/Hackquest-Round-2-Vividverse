"""
vividverse/contracts/incentive.py

Subnet-owner controlled incentive rules. Values load from **subnet_settings.json**
(same directory as this file) at import time. If the file is missing or invalid,
defaults match the historical mechanism constants.

Defines quality threshold, weight computation, and emission shares. Part of the
extraction-ready protocol layer (see contracts/PROTOCOL.md).

MOVED FROM: vividverse.validator.reward — full implementation was there.
Validator.reward now re-exports from here for backward compatibility.

Allocation rules:
  - Submissions below QUALITY_THRESHOLD receive weight 0.
  - If no submission qualifies, all weights are 0.
  - If exactly one submission qualifies, it receives weight 1.0.
  - If multiple submissions qualify:
      winner receives exactly WINNER_SHARE;
      qualifying non-winners share PROPORTIONAL_SHARE pro-rata by
      their own scores (the winner is excluded from the non-winner pool).
  - Tie-breaking is deterministic: highest score first, then lowest UID.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import torch
from typing import Dict, Tuple

# Defaults if subnet_settings.json is absent (must match platform fallbacks).
_DEFAULT_QUALITY = 75.0
_DEFAULT_WINNER = 0.70
_DEFAULT_PROP = 0.30


def _load_subnet_incentive_params() -> Tuple[float, float, float]:
    """
    Read qualityThreshold, winnerShare, proportionalShare from subnet_settings.json.
    Optional env overrides: QUALITY_THRESHOLD, WINNER_SHARE, PROPORTIONAL_SHARE (same as platform).
    """
    path = Path(__file__).resolve().parent / "subnet_settings.json"
    q, w, p = _DEFAULT_QUALITY, _DEFAULT_WINNER, _DEFAULT_PROP
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            q = float(raw.get("qualityThreshold", q))
            w = float(raw.get("winnerShare", w))
            p = float(raw.get("proportionalShare", p))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    def _env_float(name: str, fallback: float) -> float:
        env = os.environ.get(name)
        if env is not None and env.strip() != "":
            try:
                return float(env.strip())
            except ValueError:
                pass
        return fallback

    # Env overrides (operator emergency; keep in sync with platform read-config)
    q = _env_float("QUALITY_THRESHOLD", q)
    w = _env_float("WINNER_SHARE", w)
    p = _env_float("PROPORTIONAL_SHARE", p)

    if not (0.0 <= q <= 100.0):
        q = _DEFAULT_QUALITY
    if w <= 0 or p <= 0 or w > 1.0 or p > 1.0:
        w, p = _DEFAULT_WINNER, _DEFAULT_PROP
    if abs(w + p - 1.0) > 1e-3:
        # Emission split for multi-qualifier rounds must allocate exactly 100% between winner and pool.
        w, p = _DEFAULT_WINNER, _DEFAULT_PROP
    return q, w, p


QUALITY_THRESHOLD, WINNER_SHARE, PROPORTIONAL_SHARE = _load_subnet_incentive_params()


def _select_winner(qualifying: Dict[int, float]) -> int:
    """
    Deterministic winner selection from a non-empty qualifying dict.

    Priority:
      1. Highest score.
      2. Lowest UID on score tie.
    """
    return min(qualifying, key=lambda uid: (-qualifying[uid], uid))


def compute_weights(
    scores: Dict[int, float],
    n_total_uids: int,
) -> torch.Tensor:
    """
    Compute normalised Yuma weights from raw critic scores for the current round.

    Args:
        scores: Mapping of {uid: raw_score} for submissions in the current round.
        n_total_uids: Total number of UIDs in the subnet (metagraph.n).

    Returns:
        torch.Tensor of shape (n_total_uids,) with dtype float32.
        Sums to 1.0 when at least one submission qualifies; all zeros otherwise.
    """
    weights = torch.zeros(n_total_uids, dtype=torch.float32)
    if not scores:
        return weights

    for uid in scores:
        if uid < 0 or uid >= n_total_uids:
            raise ValueError(f"UID {uid} out of range [0, {n_total_uids})")

    # Step 1: filter to qualifying submissions only
    qualifying = {uid: s for uid, s in scores.items() if s >= QUALITY_THRESHOLD}
    if not qualifying:
        return weights  # all below threshold — all receive 0

    # Step 2: deterministic winner from qualifying set
    winner_uid = _select_winner(qualifying)

    # Step 3: allocate weights — winner never participates in non-winner pool
    non_winners = {uid: s for uid, s in qualifying.items() if uid != winner_uid}
    if not non_winners:
        # sole qualifying submission receives full weight
        weights[winner_uid] = 1.0
    else:
        # winner gets exactly WINNER_SHARE
        weights[winner_uid] = WINNER_SHARE
        # non-winners split PROPORTIONAL_SHARE pro-rata by their own scores
        total_non_winner = sum(non_winners.values())
        for uid, score in non_winners.items():
            weights[uid] = PROPORTIONAL_SHARE * (score / total_non_winner)

    return weights


def identify_winner(uid_to_score: dict[int, float]) -> int | None:
    """
    Return the winning UID for this round.

    Only qualifying submissions (score >= QUALITY_THRESHOLD) are eligible.
    Tie-breaking: highest score first, then lowest UID.
    Returns None if no submissions or none qualify.
    """
    if not uid_to_score:
        return None
    qualifying = {uid: s for uid, s in uid_to_score.items() if s >= QUALITY_THRESHOLD}
    if not qualifying:
        return None
    return _select_winner(qualifying)
