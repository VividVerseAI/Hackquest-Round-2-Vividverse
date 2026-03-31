"""
vividverse/contracts/scoring.py

Canonical validator scoring input contract.
Validators (or their delegated critics) score submissions.
The mechanism defines what must be present to produce a valid score.

Platform critics submit scores; the scoring *target* is mechanism-defined.

Eligibility ladder:
  has_minimum_for_score()   — structural validity: correct types, hash shape,
                              URL scheme, duration in range, hotkey length.
  is_eligible_for_scoring() — structural validity AND format_validated=True,
                              meaning validate_format() (or equivalent) confirmed
                              the artifact at intake time. Callers that require
                              confirmed artifacts must use this gate.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

# submission_hash must be a 64-character lowercase hex SHA-256 digest.
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_URL_SCHEMES = ("http://", "https://")
# Minimum duration mirrors format_validator.MIN_DURATION_SECS; imported by value
# to avoid a cross-contract dependency.
_MIN_DURATION_SECS: float = 90.0


@dataclass
class ScoringInput:
    """
    Minimum data required for a valid scored submission.
    Validators/critics receive this (or equivalent) to produce a score.

    format_validated: set to True only when validate_format() (FFprobe) or an
    equivalent artifact-level check ran at intake. Callers that want only
    confirmed artifacts should call is_eligible_for_scoring() rather than
    has_minimum_for_score().
    """
    round_id: int
    miner_hotkey: str
    submission_hash: str
    submission_url: str
    duration_seconds: float
    narrative_progression: Optional[int] = None
    # True only when validate_format() or equivalent confirmed the artifact.
    format_validated: bool = False

    def has_minimum_for_score(self) -> bool:
        """
        True if the input is structurally valid: round >= 1, hotkey present and
        at least 10 chars, hash is a 64-char lowercase hex SHA-256 digest, URL
        uses http/https, and duration is within the mechanism window.

        Does NOT check format_validated — use is_eligible_for_scoring() for that.
        """
        return bool(
            self.round_id >= 1
            and self.miner_hotkey
            and len(self.miner_hotkey) >= 10
            and bool(_SHA256_HEX_RE.match(self.submission_hash or ""))
            and self.submission_url
            and self.submission_url.startswith(_URL_SCHEMES)
            and self.duration_seconds >= _MIN_DURATION_SECS
        )

    def is_eligible_for_scoring(self) -> bool:
        """
        True if this input may receive a score:
        - Passes has_minimum_for_score() (structural validity), AND
        - format_validated is True (artifact was confirmed at intake).

        Submissions that only passed metadata-only validation have
        format_validated=False and will not pass this gate.
        """
        return self.has_minimum_for_score() and self.format_validated


@dataclass
class ScoredSubmission:
    """A submission with its score — mechanism-side representation."""
    miner_hotkey: str
    miner_uid: int
    raw_score: float
    round_id: int
