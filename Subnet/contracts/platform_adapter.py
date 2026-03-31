"""
vividverse/contracts/platform_adapter.py

Platform adapter — maps platform/prisma data to mechanism contracts.
TRANSPORT vs AUTHORITY:
- Platform APIs may be used as transport (fetch/store)
- Mechanism contracts define meaning and validation
- This module converts platform shape → mechanism shape

The platform does NOT own submission semantics, narrative state, or phase semantics.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

from vividverse.contracts.submission import SubmissionContract, validate_submission_metadata
from vividverse.contracts.narrative import NarrativeState, RoundNarrativeContext
from vividverse.contracts.artifact import ArtifactRef, ArtifactRefKind
from vividverse.contracts.phases import Phase


def synapse_to_submission_contract(
    round_id: int,
    response: Any,
    miner_hotkey: str,
) -> Optional[SubmissionContract]:
    """
    Convert SubmissionSynapse response to SubmissionContract.
    Returns None if no_submission or invalid.
    """
    if getattr(response, "no_submission", None):
        return None
    if not response.submission_hash or not response.submission_url:
        return None

    return SubmissionContract(
        round_id=round_id,
        miner_hotkey=response.miner_hotkey or miner_hotkey,
        submission_hash=response.submission_hash,
        submission_url=response.submission_url,
        duration_seconds=response.duration_seconds or 0.0,
        has_audio=response.has_audio if response.has_audio is not None else True,
        submission_timestamp=response.submission_timestamp,
        narrative_progression=response.narrative_progression,
    )


def platform_round_to_narrative_context(round_data: Dict[str, Any]) -> RoundNarrativeContext:
    """Convert platform round state (API response) to mechanism RoundNarrativeContext."""
    return RoundNarrativeContext(
        round_id=int(round_data.get("round_id", 0)),
        narrative_summary=round_data.get("narrative_summary", ""),
        established_characters=round_data.get("established_characters", ""),
        tone_and_genre=round_data.get("tone_and_genre", ""),
        selected_prompt_id=round_data.get("selected_prompt_id"),
    )


def url_to_artifact_ref(url: str, checksum: Optional[str] = None) -> ArtifactRef:
    """Create ArtifactRef from platform/object-store URL."""
    kind = ArtifactRefKind.PLATFORM if "vividverse" in url or "localhost" in url else ArtifactRefKind.OBJECT_STORE
    return ArtifactRef(ref=url, kind=kind, checksum_sha256=checksum)


def platform_phase_to_mechanism_phase(platform_phase: str) -> Optional[Phase]:
    """Map platform phase string to mechanism Phase enum."""
    m = {
        "prompt_voting": Phase.PROMPT_VOTING,
        "submission": Phase.SUBMISSION,
        "evaluation": Phase.EVALUATION,
        "finalised": Phase.FINALISED,
    }
    return m.get(platform_phase)
