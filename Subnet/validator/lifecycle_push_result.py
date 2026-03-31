"""
Structured result for POST /api/validator/lifecycle (validator → platform).

Wire format uses camelCase keys to match the Next.js API. This module maps them
to Python snake_case fields for callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


def retryable_from_http_status(status: int, code: Optional[str] = None) -> bool:
    # 202 = quorum vote recorded, waiting for more validators — not an error, not retryable.
    if status == 202:
        return False
    # WINNER_CONFLICT requires manual investigation — validators disagree on winner.
    if status == 409 and code == "WINNER_CONFLICT":
        return False
    if status == 409:
        return True
    if code == "FINALISATION_FAILED":
        return False
    if status == 422:
        return False
    if status in (400, 401, 403):
        return False
    if status in (503, 502, 429):
        return True
    if status >= 500:
        return True
    return False


@dataclass
class LifecyclePushResult:
    accepted: bool
    round_id: Optional[int]
    phase: str
    lifecycle_id: str
    lifecycle_version: int
    reason: str
    retryable: bool
    platform_phase_after_commit: Optional[str]
    platform_round_id_after_commit: Optional[int]
    idempotent: bool = False
    quorum_pending: bool = False
    vote_count: int = 0
    quorum: int = 1
    winner_conflict: bool = False
    already_finalised: bool = False

    @staticmethod
    def from_json(
        data: Dict[str, Any],
        *,
        fallback_phase: str,
        fallback_round_id: Optional[int],
        http_status: int,
    ) -> LifecyclePushResult:
        """Parse platform JSON body; fill gaps when older servers omit fields."""
        accepted = bool(data.get("accepted", http_status in (200, 201)))
        reason = str(data.get("reason") or data.get("error") or "")
        rid = data.get("roundId")
        if rid is None:
            round_id: Optional[int] = fallback_round_id
        else:
            try:
                round_id = int(rid)
            except (TypeError, ValueError):
                round_id = fallback_round_id
        phase = str(data.get("phase") or fallback_phase or "unknown")
        lifecycle_id = str(data.get("lifecycleId") or "current")
        try:
            lifecycle_version = int(data.get("lifecycleVersion") or 0)
        except (TypeError, ValueError):
            lifecycle_version = 0
        retryable = bool(
            data.get("retryable")
            if data.get("retryable") is not None
            else retryable_from_http_status(http_status, data.get("code"))
        )
        ppc = data.get("platformPhaseAfterCommit")
        prc = data.get("platformRoundIdAfterCommit")
        platform_phase_after_commit = (
            str(ppc) if ppc is not None and ppc != "" else None
        )
        platform_round_id_after_commit: Optional[int]
        if prc is None or prc == "":
            platform_round_id_after_commit = None
        else:
            try:
                platform_round_id_after_commit = int(prc)
            except (TypeError, ValueError):
                platform_round_id_after_commit = None
        idempotent = bool(data.get("idempotent", False))
        quorum_pending = bool(data.get("quorumPending", False))
        try:
            vote_count = int(data.get("voteCount") or 0)
        except (TypeError, ValueError):
            vote_count = 0
        try:
            quorum = int(data.get("quorum") or 1)
        except (TypeError, ValueError):
            quorum = 1
        winner_conflict = (
            str(data.get("code") or "").upper() == "WINNER_CONFLICT"
            or bool(data.get("winnerConflict", False))
        )
        already_finalised = bool(data.get("alreadyFinalised", False))
        return LifecyclePushResult(
            accepted=accepted,
            round_id=round_id,
            phase=phase,
            lifecycle_id=lifecycle_id,
            lifecycle_version=lifecycle_version,
            reason=reason,
            retryable=retryable,
            platform_phase_after_commit=platform_phase_after_commit,
            platform_round_id_after_commit=platform_round_id_after_commit,
            idempotent=idempotent,
            quorum_pending=quorum_pending,
            vote_count=vote_count,
            quorum=quorum,
            winner_conflict=winner_conflict,
            already_finalised=already_finalised,
        )
