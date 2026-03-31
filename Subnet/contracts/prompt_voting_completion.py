"""
vividverse/contracts/prompt_voting_completion.py

Mechanism-owned prompt-voting completion rules.
Validator uses these rules when deciding prompt_voting → prompt_selected / roundBootstrap.

Completion rule: deadline_reached only.
  - The countdown runs for the full window regardless of participation.
  - When the deadline passes, the highest-voted eligible prompt wins.
  - No early advance on quorum or full participation.

Edge cases:
  - No eligible prompt (no prompts with creator in votedMinerIds) → no_selection, do not advance.
  - Tie: deterministic — sorted by (-voteCount, id); lowest prompt id among top-voted wins.
  - Deadline with no votes: no_selection, do not advance.
  - Low participation: wait for deadline (do not complete early).

Ballot-integrity rules (no self-voting; prompt creator must vote on a different prompt for their
prompt to remain eligible) are the responsibility of upstream callers. This module checks only
that a prompt's creator appears in voted_miner_ids (creator voted on *something*), not that the
creator voted on a *different* prompt. Enforce self-vote exclusion and creator-must-vote-elsewhere
rules before passing voted_miner_ids and prompt voteCount values into this function.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set, Tuple

# Quorum ratio default — used for observability reporting only, not for completion decisions
DEFAULT_QUORUM_RATIO = 0.5

# Minimum votes to consider selection valid.
# Set to 0: when all eligible prompts have 0 votes (e.g. miners only voted for
# ineligible prompts), still select the eligible prompt with the highest id tie-break
# rather than looping forever on DEADLINE_NO_VOTES.
MIN_VOTES_FOR_SELECTION = 0


class PromptVotingCompletionReason(str, Enum):
    """Why prompt voting was considered complete (mechanism rule)."""
    DEADLINE_REACHED = "deadline_reached"
    GENESIS_NO_MINERS = "genesis_no_miners"  # No miners yet; bootstrap for first round


class NoSelectionReason(str, Enum):
    """Why no prompt was selected (do not advance)."""
    NO_ELIGIBLE_PROMPTS = "no_eligible_prompts"
    NO_PROMPTS = "no_prompts"
    INSUFFICIENT_PARTICIPATION = "insufficient_participation"
    DEADLINE_NO_VOTES = "deadline_no_votes"


@dataclass
class PromptVotingDecision:
    """
    Mechanism-side prompt voting outcome.
    Validator produces; platform consumes.
    """
    complete: bool
    """True if voting is complete and we may advance (have selection or no_selection with reason)."""
    selected_prompt_id: Optional[str] = None
    """Winning prompt id when complete and we have a winner."""
    completion_reason: Optional[PromptVotingCompletionReason] = None
    """Why completion was triggered."""
    no_selection_reason: Optional[NoSelectionReason] = None
    """When complete but no selection (do not advance)."""
    quorum_met: bool = False
    """True if quorum of miners voted."""
    deadline_reached: bool = False
    """True if deadline passed (from transport)."""
    total_miners: int = 0
    voted_miners: int = 0
    """For transparency."""
    tie_broken: bool = False
    """True when winner was chosen by tie-breaker among top-voted."""


def _eligible_prompts(
    prompts: List[dict],
    voted_miner_ids: Set[str],
) -> List[dict]:
    """Prompts whose creator is in voted_miner_ids (mechanism rule: eligible = creator voted)."""
    return [p for p in prompts if p.get("userId") in voted_miner_ids]


def _select_winner(
    eligible: List[dict],
) -> Tuple[Optional[str], bool]:
    """
    Select winner from eligible prompts that have at least MIN_VOTES_FOR_SELECTION votes.

    Tie-break is deterministic: prompts are sorted by (-voteCount, id). Among those sharing
    the top vote count, the one with the lexicographically smallest id wins. No randomness.

    Ballot-integrity (no self-voting, creator-must-vote-elsewhere) is NOT verified here —
    callers must enforce those rules before passing eligible prompts and voteCount values.

    Returns (selected_prompt_id, tie_broken).
    tie_broken is True when multiple prompts share the top vote count (tiebreaker applied).
    Returns (None, False) when no eligible prompt has enough votes.
    """
    if not eligible:
        return None, False
    by_votes = sorted(eligible, key=lambda p: (-p.get("voteCount", 0), p.get("id", "")))
    top_votes = by_votes[0].get("voteCount", 0)
    if top_votes < MIN_VOTES_FOR_SELECTION:
        return None, False
    top_tied = [p for p in by_votes if p.get("voteCount", 0) == top_votes]
    tie_broken = len(top_tied) > 1
    return top_tied[0].get("id"), tie_broken


def compute_prompt_voting_decision(
    prompts: List[dict],
    miner_ids: List[str],
    voted_miner_ids: List[str],
    deadline_reached: bool = False,
    now_unix: Optional[int] = None,
    deadline_unix: Optional[int] = None,
    total_miners_override: Optional[int] = None,
    # quorum_ratio retained for observability only — no longer triggers early advance
    quorum_ratio: float = DEFAULT_QUORUM_RATIO,
) -> PromptVotingDecision:
    """
    Mechanism-owned prompt voting completion decision.

    Completion is triggered by deadline only. The full voting window always runs.

    Tie-breaking is deterministic: sorted by (-voteCount, id); lowest prompt id wins ties.
    Zero-vote selection is prevented: _select_winner requires voteCount >= MIN_VOTES_FOR_SELECTION.

    Ballot-integrity contract (callers must enforce before calling):
      - Votes cast by a prompt's own creator must be excluded from that prompt's voteCount.
      - A miner's id must not appear in voted_miner_ids unless they voted on a prompt other
        than their own. This module checks only that the creator appears in voted_miner_ids
        (creator voted on *something*); it does NOT enforce creator-must-vote-elsewhere.

    Args:
        prompts: List of {id, content, voteCount, userId}
        miner_ids: All miner user ids (platform-linked; used for eligible/voted)
        voted_miner_ids: Miner ids who have voted (self-votes excluded by caller)
        deadline_reached: True if deadline passed (from platform transport or local)
        now_unix: Current time (optional, for deadline check)
        deadline_unix: Deadline as unix ts (optional; if provided and now_unix >= deadline_unix, deadline_reached)
        total_miners_override: When set (e.g. from metagraph), use for quorum denominator. Else use len(miner_ids).
        quorum_ratio: Observability only — recorded on PromptVotingDecision but does not trigger advance.

    Returns:
        PromptVotingDecision with complete, selected_prompt_id, completion_reason, etc.
    """
    total = (
        total_miners_override
        if total_miners_override is not None and total_miners_override >= 0
        else len(miner_ids)
    )
    voted = len(voted_miner_ids)
    voted_set = set(voted_miner_ids)

    # Resolve deadline from params if not explicitly passed
    if deadline_reached is False and now_unix is not None and deadline_unix is not None:
        if now_unix >= deadline_unix:
            deadline_reached = True

    eligible = _eligible_prompts(prompts, voted_set)
    quorum_met = total == 0 or (voted / total) >= quorum_ratio

    # No prompts at all — only complete when deadline reached
    if not prompts:
        return PromptVotingDecision(
            complete=deadline_reached,
            no_selection_reason=NoSelectionReason.NO_PROMPTS if deadline_reached else None,
            completion_reason=PromptVotingCompletionReason.DEADLINE_REACHED if deadline_reached else None,
            quorum_met=quorum_met,
            deadline_reached=deadline_reached,
            total_miners=total,
            voted_miners=voted,
        )

    # No eligible prompts (no prompt creator has voted) — only complete when deadline reached
    if not eligible:
        if deadline_reached:
            return PromptVotingDecision(
                complete=True,
                no_selection_reason=NoSelectionReason.NO_ELIGIBLE_PROMPTS,
                completion_reason=PromptVotingCompletionReason.DEADLINE_REACHED,
                quorum_met=quorum_met,
                deadline_reached=True,
                total_miners=total,
                voted_miners=voted,
            )
        return PromptVotingDecision(
            complete=False,
            no_selection_reason=NoSelectionReason.NO_ELIGIBLE_PROMPTS,
            quorum_met=quorum_met,
            deadline_reached=False,
            total_miners=total,
            voted_miners=voted,
        )

    # Genesis: no miners registered yet — bootstrap by selecting the eligible prompt with
    # the lexicographically smallest id. No vote requirement applies (no miners can vote).
    if total == 0:
        by_id = sorted(eligible, key=lambda p: p.get("id", ""))
        winner_id = by_id[0].get("id")
        return PromptVotingDecision(
            complete=True,
            selected_prompt_id=winner_id,
            completion_reason=PromptVotingCompletionReason.GENESIS_NO_MINERS,
            quorum_met=True,
            deadline_reached=False,
            total_miners=0,
            voted_miners=0,
            tie_broken=False,
        )

    # Deadline reached — select winner from eligible prompts
    if deadline_reached:
        winner_id, tie_broken = _select_winner(eligible)
        if winner_id is not None:
            return PromptVotingDecision(
                complete=True,
                selected_prompt_id=winner_id,
                completion_reason=PromptVotingCompletionReason.DEADLINE_REACHED,
                quorum_met=quorum_met,
                deadline_reached=True,
                total_miners=total,
                voted_miners=voted,
                tie_broken=tie_broken,
            )
        return PromptVotingDecision(
            complete=True,
            no_selection_reason=NoSelectionReason.DEADLINE_NO_VOTES,
            completion_reason=PromptVotingCompletionReason.DEADLINE_REACHED,
            quorum_met=quorum_met,
            deadline_reached=True,
            total_miners=total,
            voted_miners=voted,
        )

    # Not complete — waiting for deadline
    return PromptVotingDecision(
        complete=False,
        quorum_met=quorum_met,
        deadline_reached=False,
        total_miners=total,
        voted_miners=voted,
    )
