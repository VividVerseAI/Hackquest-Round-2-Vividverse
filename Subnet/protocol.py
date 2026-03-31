"""
vividverse/protocol.py

Defines the wire protocol (Synapses) exchanged between validators and miners.

CANONICAL CONTRACT: The mechanism defines submission semantics.
  See vividverse.contracts.submission.SubmissionContract for the canonical schema.
  This Synapse is the wire format; it maps to the contract.

SDK Reference: bt.Synapse
  https://docs.learnbittensor.org/sdk/bt-api-ref
  Synapses are Pydantic models — all fields need type annotations.
  Validators fill request fields; miners fill response fields.
  Fields marked Optional are filled by the responder.
"""

from __future__ import annotations
from typing import Optional
import bittensor as bt


class RoundStateQuery(bt.Synapse):
    """
    Sent by validator → miner at the start of each round.

    Purpose: Inform miners of the current round context so they know
    what canonical chain they must build upon.

    Request fields (filled by validator before sending):
        round_id, round_phase, submission_deadline_unix,
        canonical_chain_length, canonical_chain_hash,
        narrative_summary, established_characters, tone_and_genre

    Response fields (filled by miner):
        submission_acknowledged
    """

    # ── Request fields (set by validator) ────────────────────────────────────
    round_id: int = 0
    # Phase values: "submission" | "evaluation" | "finalised"
    round_phase: str = "submission"
    # Unix timestamp — when the submission window closes
    submission_deadline_unix: int = 0
    # How many scenes are in the canonical chain so far (0 = film just started)
    canonical_chain_length: int = 0
    # SHA-256 of the full canonical chain manifest — miners use this to verify
    # they have the correct chain before generating
    canonical_chain_hash: str = ""
    # Human-readable context for the miner — what has happened in the story
    narrative_summary: str = ""
    established_characters: str = ""
    tone_and_genre: str = ""

    # ── Response fields (set by miner) ───────────────────────────────────────
    # Preserved for backward compatibility — still set by all miners.
    # Prefer ack_round_id == round_id for richer validation.
    submission_acknowledged: Optional[bool] = None
    # Structured echo of the round context the miner received.
    # Allows the validator to confirm which round was acknowledged.
    ack_round_id: Optional[int] = None
    ack_round_phase: Optional[str] = None
    ack_canonical_chain_hash: Optional[str] = None
    ack_submission_deadline_unix: Optional[int] = None
    # Miner's self-reported hotkey — audit metadata only.
    # Validator must use metagraph for authoritative identity.
    ack_miner_hotkey: Optional[str] = None


class SubmissionSynapse(bt.Synapse):
    """
    Sent by validator → miner during evaluation phase.

    Purpose: Validator requests the miner's submission metadata for the
    current round. The miner returns a file hash and URL — the validator
    then fetches and validates the file independently.

    IMPORTANT: We do NOT send the MP4 through the Synapse.
    Synapses are not designed for large binary payloads.
    The miner hosts the file; the validator fetches it via submission_url.

    Request fields:
        round_id  (validator specifies which round it is querying)

    Response fields:
        All Optional fields below (filled by miner).
        If no_submission is True, all other response fields will be None.
    """

    # ── Request field ─────────────────────────────────────────────────────────
    round_id: int = 0

    # ── Response fields ───────────────────────────────────────────────────────
    # SHA-256 hex digest of the MP4 file — used for duplicate detection
    submission_hash: Optional[str] = None
    # URL where the validator can GET the MP4 file
    submission_url: Optional[str] = None
    # Self-reported duration in seconds — validator will verify via FFprobe
    duration_seconds: Optional[float] = None
    # Whether the submission includes audio (None = unknown)
    has_audio: Optional[bool] = None
    # Miner's self-reported hotkey — audit metadata only.
    # Validator must use metagraph for authoritative identity.
    miner_hotkey: Optional[str] = None
    # Unix timestamp of when the miner registered this submission (not response time)
    submission_timestamp: Optional[int] = None
    # Set to True if the miner did not submit anything for this round
    no_submission: Optional[bool] = None
    # 0–100: miner's view of where the story is (Round 2+). Mirrors Platform Submission.
    narrative_progression: Optional[int] = None

    # ── Structured response envelope (set by miner) ──────────────────────────
    # The round_id the miner is responding for — validator cross-checks against request.
    response_round_id: Optional[int] = None
    # Phase during which the submission was registered, if known.
    response_phase: Optional[str] = None
    # Chain hash the miner was aware of at submission time, if known.
    response_canonical_chain_hash: Optional[str] = None
    # Structured status code.
    # Values: ok | no_submission | round_mismatch | chain_mismatch |
    #         submission_not_ready | internal_error | invalid_request
    response_status: Optional[str] = None
    # Human-readable detail for response_status (audit / debugging only).
    response_reason: Optional[str] = None
