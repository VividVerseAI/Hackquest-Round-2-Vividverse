"""
vividverse/validator/reward.py

Re-exports incentive logic from contracts. Canonical definitions live in
vividverse.contracts.incentive (extraction-ready protocol layer).

REMOVED (moved to contracts/incentive.py):
  - Full implementation of QUALITY_THRESHOLD, WINNER_SHARE, PROPORTIONAL_SHARE
  - compute_weights(), identify_winner()
  These were here previously; now defined in contracts for subnet-owner control.
  This file kept for backward compatibility (neurons import from validator.reward).
"""
from vividverse.contracts.incentive import (
    QUALITY_THRESHOLD,
    WINNER_SHARE,
    PROPORTIONAL_SHARE,
    compute_weights,
    identify_winner,
)

__all__ = [
    "QUALITY_THRESHOLD",
    "WINNER_SHARE",
    "PROPORTIONAL_SHARE",
    "compute_weights",
    "identify_winner",
]
