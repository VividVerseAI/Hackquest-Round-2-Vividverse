"""
vividverse/contracts/phases.py

Canonical mechanism phase and cadence contract.
The mechanism defines phases and valid transitions.
Platform scheduler may trigger transitions; mechanism owns the semantics.
"""
from __future__ import annotations
from enum import Enum
from typing import Dict, Set, Tuple


class Phase(str, Enum):
    """Mechanism phases — full lifecycle including prompt selection."""
    PROMPT_VOTING = "prompt_voting"
    PROMPT_SELECTED = "prompt_selected"  # Brief: winner chosen, round not yet created
    SUBMISSION = "submission"
    EVALUATION = "evaluation"
    FINALISED = "finalised"
    RESTART = "restart"  # Round restarted; back to submission
    RETURN_TO_PROMPT_VOTING = "return_to_prompt_voting"  # Film cycle ends; new cycle


PHASES = list(Phase)

# Valid transitions: (from_phase, to_phase)
# Platform scheduler/validator may trigger; mechanism defines validity
ValidTransitions: Set[Tuple[Phase, Phase]] = {
    (Phase.PROMPT_VOTING, Phase.PROMPT_SELECTED),
    (Phase.PROMPT_SELECTED, Phase.SUBMISSION),
    (Phase.SUBMISSION, Phase.EVALUATION),
    (Phase.EVALUATION, Phase.FINALISED),
    (Phase.EVALUATION, Phase.RESTART),
    (Phase.RESTART, Phase.SUBMISSION),
    (Phase.FINALISED, Phase.RETURN_TO_PROMPT_VOTING),
    (Phase.RETURN_TO_PROMPT_VOTING, Phase.PROMPT_VOTING),
}


def get_valid_transitions(from_phase: Phase) -> Set[Phase]:
    """Return phases that can legally follow from_phase."""
    return {t[1] for t in ValidTransitions if t[0] == from_phase}


def is_valid_transition(from_phase: Phase, to_phase: Phase) -> bool:
    """True if the transition is allowed."""
    return (from_phase, to_phase) in ValidTransitions


# Transition source — who produced the transition
TransitionSource = str  # "validator_produced" | "platform_fallback" | "scheduler_triggered"
VALID_TRANSITION_SOURCES = frozenset({
    "validator_produced",
    "platform_fallback",
    "scheduler_triggered",
})


def is_valid_transition_source(source: str) -> bool:
    """True if source is a valid transition source."""
    return source in VALID_TRANSITION_SOURCES


# Participant expectations per phase (documentation)
PHASE_PARTICIPANT_EXPECTATIONS: Dict[Phase, str] = {
    Phase.PROMPT_VOTING: "Miners vote on prompts. Platform stores votes. Validator may compute winner.",
    Phase.PROMPT_SELECTED: "Winner chosen. Round created. Transition to submission.",
    Phase.SUBMISSION: "Miners generate and submit video segments. Validator broadcasts round state.",
    Phase.EVALUATION: "Validator collects submissions. Critics score (platform-mediated). Validator sets weights.",
    Phase.FINALISED: "Weights set. Canonical chain updated. Film cycle may end.",
    Phase.RESTART: "All scores below threshold. Round restarted; miners resubmit.",
    Phase.RETURN_TO_PROMPT_VOTING: "Film finalised. New film cycle begins with prompt voting.",
}
