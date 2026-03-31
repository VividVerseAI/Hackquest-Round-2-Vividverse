"""
vividverse/contracts/round_registry.py

Mechanism-side round registry / round identity model.
Platform persists rounds; mechanism defines canonical identity and chaining.

No on-chain round storage exists. These structures define the canonical round
creation, bootstrap, parent linkage, and chain position. Platform stores
mechanism-defined round model as transport.

REMOVED (now in cadence.py, no env overrides):
  - DEFAULT_SUBMISSION_WINDOW_SEC, DEFAULT_EVALUATION_WINDOW_SEC (were from
    os.environ.get("SUBMISSION_WINDOW_SEC"), os.environ.get("EVALUATION_WINDOW_SEC"))
  - VIVIDVERSE_FAST_ROUNDS: when "1", used 30s windows instead of default.
    Cadence is now fixed; subnet owner controls via what they ship.

See docs/NEXT_PHASE_VALIDATOR_AUTHORITY.md.
"""
from __future__ import annotations
import time

from vividverse.contracts.cadence import (
    SUBMISSION_WINDOW_SEC,
    EVALUATION_WINDOW_SEC,
)

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RoundBootstrapReason(str, Enum):
    """Why a round was created (mechanism semantics)."""
    PROMPT_VOTING = "prompt_voting"
    CONTINUATION = "continuation"
    RESTART = "restart"


class RoundSource(str, Enum):
    """Who produced round creation decision."""
    VALIDATOR_PRODUCED = "validator_produced"
    PLATFORM_FALLBACK = "platform_fallback"
    SCHEDULER_TRIGGERED = "scheduler_triggered"


@dataclass
class MechanismRoundId:
    """
    Mechanism-owned round identifier.
    Platform roundId (int) maps to this; mechanism defines identity.
    """
    round_id: int
    chain_position: int  # 1-based index in film cycle chain


@dataclass
class RoundCreationIdentity:
    """
    Identity of round creation — who decided, why, what seeded it.
    Validator produces; platform persists.
    """
    selected_prompt_id: str
    bootstrap_reason: RoundBootstrapReason
    source: RoundSource
    transition_reason: Optional[str] = None
    completion_reason: Optional[str] = None
    parent_round_id: Optional[int] = None
    narrative_seed: Optional[str] = None
    restart_lineage: bool = False  # True when this round was created by restart


@dataclass
class RoundChainLink:
    """
    One link in the round chain — parent linkage and chain position.
    """
    round_id: int
    parent_round_id: Optional[int]
    chain_position: int
    is_restart: bool


@dataclass
class RoundRegistryEntry:
    """
    Full mechanism-side round registry entry.
    Platform persists; mechanism defines structure.
    """
    round_id: int
    creation_identity: RoundCreationIdentity
    chain_link: RoundChainLink
    selected_prompt_origin: str  # prompt id that seeded this round


@dataclass
class RoundCreationOutcome:
    """
    Outcome of round creation attempt.
    Decider: validator (or platform fallback). Executor: platform.
    """
    success: bool
    round_id: Optional[int] = None
    creation_identity: Optional[RoundCreationIdentity] = None
    error: Optional[str] = None


def compute_round_deadlines(now_unix: Optional[int] = None) -> tuple[int, int]:
    """
    Compute submission and evaluation deadlines for a new round.
    Mechanism-owned. Validator uses when pushing roundBootstrap.
    Cadence is subnet-owner controlled; no overrides.

    Returns:
        (submission_deadline_unix, evaluation_deadline_unix)
    """
    now = now_unix if now_unix is not None else int(time.time())
    sub_deadline = now + SUBMISSION_WINDOW_SEC
    eval_deadline = sub_deadline + EVALUATION_WINDOW_SEC
    return sub_deadline, eval_deadline


def make_round_creation_identity(
    selected_prompt_id: str,
    bootstrap_reason: RoundBootstrapReason = RoundBootstrapReason.PROMPT_VOTING,
    source: RoundSource = RoundSource.VALIDATOR_PRODUCED,
    transition_reason: Optional[str] = None,
    completion_reason: Optional[str] = None,
    parent_round_id: Optional[int] = None,
    narrative_seed: Optional[str] = None,
    restart_lineage: bool = False,
) -> RoundCreationIdentity:
    """Build RoundCreationIdentity from validator roundBootstrap payload."""
    return RoundCreationIdentity(
        selected_prompt_id=selected_prompt_id,
        bootstrap_reason=bootstrap_reason,
        source=source,
        transition_reason=transition_reason,
        completion_reason=completion_reason,
        parent_round_id=parent_round_id,
        narrative_seed=narrative_seed,
        restart_lineage=restart_lineage,
    )
