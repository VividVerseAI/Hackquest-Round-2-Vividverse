"""
vividverse/contracts/submission.py

Canonical mechanism submission contract.
Miners produce submissions; validators score them.
The platform collects/stores; it does not define what a submission is.

Required vs optional:
- REQUIRED: round_id, miner_hotkey, submission_hash, submission_url, duration_seconds
- OPTIONAL: narrative_progression (round 2+), segment bounds, continuity refs
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from vividverse.contracts.scoring import ScoringInput

# submission_hash must be a 64-character lowercase hex SHA-256 digest.
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_URL_SCHEMES = ("http://", "https://")
_URL_MIN_LEN = 10       # http://x.y at minimum
_COMMENT_MAX_LEN = 1000


@dataclass
class SubmissionContract:
    """
    Canonical submission structure — mechanism-defined.
    Maps to SubmissionSynapse (wire) and SubmissionRecord (validator store).
    Platform Submission model adapts to this.
    """
    # REQUIRED
    round_id: int
    miner_hotkey: str
    submission_hash: str
    submission_url: str
    duration_seconds: float

    # OPTIONAL — miner-provided metadata
    has_audio: bool = True
    submission_timestamp: Optional[int] = None
    narrative_progression: Optional[int] = None  # 0-100, round 2+
    # Segment bounds for continuity (when appending to prior canonical)
    segment_start_seconds: Optional[float] = None
    segment_end_seconds: Optional[float] = None
    comment: Optional[str] = None  # Optional miner comment

    # Filled by validator/store
    passed_format: bool = False
    storage_ref: Optional[str] = None  # Platform storage ref; adapter concern

    def to_scoring_input(self) -> "ScoringInput":
        """Produce the minimal scoring input for validators."""
        from vividverse.contracts.scoring import ScoringInput  # Avoid circular import
        return ScoringInput(
            round_id=self.round_id,
            miner_hotkey=self.miner_hotkey,
            submission_hash=self.submission_hash,
            submission_url=self.submission_url,
            duration_seconds=self.duration_seconds,
            narrative_progression=self.narrative_progression,
            format_validated=self.passed_format,
        )


@dataclass
class SubmissionValidationResult:
    valid: bool
    reason: str


def validate_submission_metadata(
    round_id: int,
    miner_hotkey: str,
    submission_hash: Optional[str],
    submission_url: Optional[str],
    duration_seconds: Optional[float],
    narrative_progression: Optional[int] = None,
    segment_start_seconds: Optional[float] = None,
    segment_end_seconds: Optional[float] = None,
    comment: Optional[str] = None,
    min_duration: float = 90.0,  # 1 minute 30 seconds
    max_duration: float = 600.0,
) -> SubmissionValidationResult:
    """
    Validate submission metadata against mechanism requirements.
    Does NOT validate the actual file (that requires fetch + FFprobe).

    hash: must be a 64-character lowercase hex SHA-256 digest.
    url: must be http or https.
    narrative_progression: if provided, must be [0, 100]; for round 1, must be 0.
    segment_start/end: when provided, start >= 0 and end > start.
    comment: at most _COMMENT_MAX_LEN characters.
    """
    # round_id — checked first so narrative_progression rule can reference it
    if round_id < 1:
        return SubmissionValidationResult(False, "round_id must be >= 1")

    if not miner_hotkey or len(miner_hotkey) < 10:
        return SubmissionValidationResult(False, "Missing or invalid miner_hotkey")

    # Hash: must be exactly 64 lowercase hex chars (SHA-256)
    if not submission_hash or not _SHA256_HEX_RE.match(submission_hash):
        return SubmissionValidationResult(
            False,
            "submission_hash must be a 64-character lowercase hex SHA-256 digest",
        )

    # URL: must be http/https and at least minimally well-formed
    if not submission_url or len(submission_url) < _URL_MIN_LEN:
        return SubmissionValidationResult(False, "Missing or too-short submission_url")
    if not submission_url.startswith(_URL_SCHEMES):
        return SubmissionValidationResult(False, "submission_url must use http or https scheme")

    if duration_seconds is None:
        return SubmissionValidationResult(False, "Missing duration_seconds")
    if duration_seconds < min_duration:
        return SubmissionValidationResult(
            False, f"Duration {duration_seconds}s below minimum {min_duration}s"
        )
    if duration_seconds > max_duration:
        return SubmissionValidationResult(
            False, f"Duration {duration_seconds}s above maximum {max_duration}s"
        )

    # narrative_progression: integer in [0, 100]; round 1 must be 0 (no prior chain)
    if narrative_progression is not None:
        if not isinstance(narrative_progression, int) or narrative_progression < 0 or narrative_progression > 100:
            return SubmissionValidationResult(
                False, "narrative_progression must be an integer in [0, 100]"
            )
        if round_id == 1 and narrative_progression != 0:
            return SubmissionValidationResult(
                False, "narrative_progression must be 0 for round 1 (no prior canonical chain)"
            )

    # Segment bounds: optional continuity fields
    if segment_start_seconds is not None and segment_start_seconds < 0:
        return SubmissionValidationResult(
            False, "segment_start_seconds must be non-negative"
        )
    if segment_end_seconds is not None:
        if segment_start_seconds is None:
            return SubmissionValidationResult(
                False, "segment_end_seconds requires segment_start_seconds"
            )
        if segment_end_seconds <= segment_start_seconds:
            return SubmissionValidationResult(
                False, "segment_end_seconds must be greater than segment_start_seconds"
            )

    # Comment length
    if comment is not None and len(comment) > _COMMENT_MAX_LEN:
        return SubmissionValidationResult(
            False, f"comment exceeds maximum length of {_COMMENT_MAX_LEN} characters"
        )

    return SubmissionValidationResult(True, "OK")
