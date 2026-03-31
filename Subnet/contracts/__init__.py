"""
vividverse/contracts/

Canonical mechanism contracts — the subnet/mechanism defines these.
The platform adapts, stores, and displays them; it does not own their semantics.

See docs/MECHANISM_CONTRACTS.md.
"""
from vividverse.contracts.artifact import ArtifactRef, ArtifactRefKind
from vividverse.contracts.submission import (
    SubmissionContract,
    validate_submission_metadata,
    SubmissionValidationResult,
)
from vividverse.contracts.narrative import (
    NarrativeState,
    RoundNarrativeContext,
    ContinuityReference,
)
from vividverse.contracts.phases import (
    Phase,
    ValidTransitions,
    PHASES,
    get_valid_transitions,
)
from vividverse.contracts.scoring import (
    ScoringInput,
    ScoredSubmission,
)
from vividverse.contracts.platform_adapter import (
    synapse_to_submission_contract,
    platform_round_to_narrative_context,
    url_to_artifact_ref,
    platform_phase_to_mechanism_phase,
)
from vividverse.contracts.round_registry import (
    MechanismRoundId,
    RoundRegistryEntry,
    RoundChainLink,
    RoundCreationIdentity,
    RoundCreationOutcome,
    RoundBootstrapReason,
    RoundSource,
    make_round_creation_identity,
    compute_round_deadlines,
)
from vividverse.contracts.cadence import (
    SUBMISSION_WINDOW_SEC,
    EVALUATION_WINDOW_SEC,
    PROMPT_VOTING_WINDOW_SEC,
    MINER_COUNT_FOR_COUNTDOWN,
    MIN_VOTED_MINERS_FOR_COUNTDOWN,
)
from vividverse.contracts.incentive import (
    QUALITY_THRESHOLD,
    WINNER_SHARE,
    PROPORTIONAL_SHARE,
    compute_weights,
    identify_winner,
)

__all__ = [
    "ArtifactRef",
    "ArtifactRefKind",
    "SubmissionContract",
    "validate_submission_metadata",
    "SubmissionValidationResult",
    "NarrativeState",
    "RoundNarrativeContext",
    "ContinuityReference",
    "Phase",
    "ValidTransitions",
    "PHASES",
    "get_valid_transitions",
    "ScoringInput",
    "ScoredSubmission",
    "synapse_to_submission_contract",
    "platform_round_to_narrative_context",
    "url_to_artifact_ref",
    "platform_phase_to_mechanism_phase",
    "MechanismRoundId",
    "RoundRegistryEntry",
    "RoundChainLink",
    "RoundCreationIdentity",
    "RoundCreationOutcome",
    "RoundBootstrapReason",
    "RoundSource",
    "make_round_creation_identity",
    "compute_round_deadlines",
    "SUBMISSION_WINDOW_SEC",
    "EVALUATION_WINDOW_SEC",
    "PROMPT_VOTING_WINDOW_SEC",
    "MINER_COUNT_FOR_COUNTDOWN",
    "MIN_VOTED_MINERS_FOR_COUNTDOWN",
    "QUALITY_THRESHOLD",
    "WINNER_SHARE",
    "PROPORTIONAL_SHARE",
    "compute_weights",
    "identify_winner",
]
