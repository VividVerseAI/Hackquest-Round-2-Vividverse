"""
vividverse/contracts/artifact_validation.py

Validator-side artifact validation before scoring.
Ensures artifact is accessible and meets mechanism constraints.

Validation scope:
  validate_artifact_metadata  — shape checks only; no network.
  check_artifact_url_available — reachability only (HEAD); does NOT verify
      that the returned content is a genuine, unmodified submission file.
      Hash-to-content verification requires fetching the full body and
      recomputing SHA-256 — that step is explicitly deferred (expensive).
"""
from __future__ import annotations
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

# Mechanism constraints — must match format_validator.py
MIN_DURATION_SECS = 90.0  # 1 minute 30 seconds
MAX_DURATION_SECS = 600.0

# submission_hash must be a 64-character lowercase hex SHA-256 digest.
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_URL_SCHEMES = ("http://", "https://")
_URL_MIN_LEN = 10

# Content-type prefixes that definitively indicate non-video content.
# video/*, application/octet-stream, and unknown types are allowed through.
_REJECTED_CONTENT_TYPE_PREFIXES = (
    "text/html",
    "text/plain",
    "text/xml",
    "text/css",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-www-form-urlencoded",
)


@dataclass
class ArtifactValidationResult:
    """Result of artifact availability/validation check."""
    valid: bool
    reason: str
    status_code: Optional[int] = None
    content_type: Optional[str] = None


def check_artifact_url_available(
    url: str,
    timeout: float = 20.0,
) -> ArtifactValidationResult:
    """
    HEAD request to verify artifact URL is reachable and plausibly a video.

    Scope: reachability + content-type sanity only. A passing result does NOT
    prove the content is the submitted file or that the hash matches — full
    hash-to-content verification requires fetching the body (deferred, expensive).
    """
    if not url or len(url) < _URL_MIN_LEN:
        return ArtifactValidationResult(False, "Empty or invalid URL")
    if not url.startswith(_URL_SCHEMES):
        return ArtifactValidationResult(False, "URL must be http or https")

    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            if code != 200:
                return ArtifactValidationResult(
                    False,
                    f"Artifact returned status {code}",
                    status_code=code,
                )
            ct = resp.headers.get("Content-Type", "")
            # Strip parameters (e.g. "text/html; charset=utf-8" → "text/html")
            ct_base = ct.split(";")[0].strip().lower()
            if ct_base and any(ct_base.startswith(p) for p in _REJECTED_CONTENT_TYPE_PREFIXES):
                return ArtifactValidationResult(
                    False,
                    f"Content-Type {ct_base!r} indicates non-video content",
                    status_code=code,
                    content_type=ct or None,
                )
            return ArtifactValidationResult(
                True,
                "OK",
                status_code=code,
                content_type=ct or None,
            )
    except urllib.error.HTTPError as e:
        return ArtifactValidationResult(
            False,
            f"Artifact not accessible: {e.code}",
            status_code=e.code,
        )
    except urllib.error.URLError as e:
        return ArtifactValidationResult(False, f"Artifact unreachable: {e.reason}")
    except Exception as e:
        return ArtifactValidationResult(False, f"Artifact check failed: {e}")


def validate_artifact_metadata(
    submission_hash: Optional[str],
    submission_url: Optional[str],
    duration_seconds: Optional[float],
) -> ArtifactValidationResult:
    """
    Validate artifact metadata shape against mechanism constraints.
    No network check — use check_artifact_url_available for reachability.

    hash: must be a 64-character lowercase hex SHA-256 digest.
    url: must be http or https and at least minimally well-formed.
    """
    if not submission_hash or not _SHA256_HEX_RE.match(submission_hash):
        return ArtifactValidationResult(
            False,
            "submission_hash must be a 64-character lowercase hex SHA-256 digest",
        )
    if not submission_url or len(submission_url) < _URL_MIN_LEN:
        return ArtifactValidationResult(False, "Missing or too-short submission_url")
    if not submission_url.startswith(_URL_SCHEMES):
        return ArtifactValidationResult(False, "submission_url must use http or https scheme")
    if duration_seconds is None:
        return ArtifactValidationResult(False, "Missing duration_seconds")
    if duration_seconds < MIN_DURATION_SECS:
        return ArtifactValidationResult(
            False,
            f"Duration {duration_seconds}s below minimum {MIN_DURATION_SECS}s",
        )
    if duration_seconds > MAX_DURATION_SECS:
        return ArtifactValidationResult(
            False,
            f"Duration {duration_seconds}s above maximum {MAX_DURATION_SECS}s",
        )
    return ArtifactValidationResult(True, "OK")
