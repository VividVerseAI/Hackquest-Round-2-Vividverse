"""
vividverse/utils/platform_round.py

Platform round state helpers — no bittensor dependency.
Submissions and round state are owned by the platform; these helpers
extract fields from Platform API dict responses.
"""

from __future__ import annotations
from typing import Dict, Any, Optional

from vividverse.contracts.round_registry import compute_round_deadlines


def get_round_id(round_mgr: Dict[str, Any]) -> int:
    """Get round_id from a Platform API dict response."""
    return int(round_mgr.get("round_id", 0))


def deadline_only_round_bootstrap(
    state: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Minimal roundBootstrap (deadline unix fields only) so Platform can create a Round row
    after a cold DB wipe. Full bootstrap still requires Prompt rows on Platform.
    """
    if not state:
        sub2, ev2 = compute_round_deadlines()
        return {"submissionDeadlineUnix": sub2, "evaluationDeadlineUnix": ev2}
    sub = int(state.get("submission_deadline_unix", 0) or 0)
    ev = int(state.get("evaluation_deadline_unix", 0) or 0)
    if sub > 0 and ev > 0:
        return {"submissionDeadlineUnix": sub, "evaluationDeadlineUnix": ev}
    sub2, ev2 = compute_round_deadlines()
    return {"submissionDeadlineUnix": sub2, "evaluationDeadlineUnix": ev2}
