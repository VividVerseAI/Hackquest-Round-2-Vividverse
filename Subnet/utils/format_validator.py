"""
vividverse/utils/format_validator.py

MP4 format validation using FFprobe.

Validation ladder (weakest → strongest):
  validate_metadata_only()       — duration + hotkey only; no file, no URL check.
                                   Use only in tests and mock paths.
  validate_submission_for_intake() — metadata + URL scheme + hash shape; no file
                                   download. Use in the live validator intake path.
  validate_format()              — full FFprobe on a local file; authoritative.

has_audio from the miner is UNVERIFIED at all levels below validate_format().
It must not be used as a scoring eligibility gate. Only validate_format() on
the actual file can confirm audio stream presence.
"""

from __future__ import annotations
import re
import subprocess
import hashlib
import json
from pathlib import Path
from typing import Optional, Tuple

# submission_hash must be a 64-character lowercase hex SHA-256 digest.
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_URL_SCHEMES = ("http://", "https://")

# Target format for Vividverse submissions
REQUIRED_CODEC: str = "h264"
REQUIRED_WIDTH: int = 1920
REQUIRED_HEIGHT: int = 1080
REQUIRED_FPS: float = 24.0
FPS_TOLERANCE: float = 0.5
MIN_DURATION_SECS: float = 90.0   # 1 minute 30 seconds
MAX_DURATION_SECS: float = 600.0   # 10 minutes


def validate_format(file_path: str) -> Tuple[bool, str]:
    """
    Run FFprobe on the file and verify it meets format requirements.

    Returns:
        (passed: bool, reason: str)
        If passed is True, reason is "OK".
        If passed is False, reason explains which requirement failed.
    """
    path = Path(file_path)
    if not path.exists():
        return False, f"File not found: {file_path}"

    try:
        probe = _run_probe(path)
    except Exception as e:
        return False, f"FFprobe failed: {e}"

    # Find video stream
    video_stream = None
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if video_stream is None:
        return False, "No video stream found"

    # Codec check
    codec = video_stream.get("codec_name", "").lower()
    if codec != REQUIRED_CODEC:
        return False, f"Invalid codec '{codec}' — required: h264"

    # Resolution check
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    if width != REQUIRED_WIDTH or height != REQUIRED_HEIGHT:
        return False, f"Invalid resolution {width}×{height} — required: {REQUIRED_WIDTH}×{REQUIRED_HEIGHT}"

    # Frame rate check
    fps_str = video_stream.get("r_frame_rate", "0/1")
    fps = _parse_fps(fps_str)
    if abs(fps - REQUIRED_FPS) > FPS_TOLERANCE:
        return False, f"Invalid frame rate {fps:.3f}fps — required: {REQUIRED_FPS}fps (±{FPS_TOLERANCE})"

    # Duration check — prefer container-level duration
    duration_str = probe.get("format", {}).get("duration", "0")
    try:
        duration = float(duration_str)
    except ValueError:
        return False, "Could not parse duration from file"

    if duration < MIN_DURATION_SECS:
        return False, f"Too short: {duration:.1f}s — minimum {MIN_DURATION_SECS}s (1m 30s)"
    if duration > MAX_DURATION_SECS:
        return False, f"Too long: {duration:.1f}s — maximum {MAX_DURATION_SECS}s (10 minutes)"

    return True, "OK"


def compute_file_hash(file_path: str) -> str:
    """SHA-256 of file contents — used for duplicate detection."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def validate_submission_for_intake(
    claimed_duration: float,
    claimed_hotkey: str,
    submission_url: str,
    submission_hash: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Intake-path validation: metadata checks plus URL scheme and hash shape.

    Stronger than validate_metadata_only: additionally verifies that the URL
    uses http/https and, when a hash is provided, that it matches SHA-256 hex
    shape. Does NOT download the file or probe the URL.

    IMPORTANT: has_audio from the miner is NOT verified here and must NOT be
    used as a scoring eligibility gate — only validate_format() on the actual
    file can confirm audio presence.

    A True result here does not constitute full format validation.
    passed_format=True in the store requires validate_format() to have run,
    or explicit operator acknowledgement of metadata-only intake.
    """
    if claimed_duration < MIN_DURATION_SECS or claimed_duration > MAX_DURATION_SECS:
        return False, f"Duration {claimed_duration}s out of range [{MIN_DURATION_SECS}, {MAX_DURATION_SECS}]"
    if not claimed_hotkey or len(claimed_hotkey) < 10:
        return False, "Invalid or missing hotkey"
    if not submission_url or not submission_url.startswith(_URL_SCHEMES):
        return False, "submission_url must use http or https scheme"
    if submission_hash is not None and not _SHA256_HEX_RE.match(submission_hash):
        return False, "submission_hash must be a 64-character lowercase hex SHA-256 digest"
    return True, "OK (metadata + URL scheme — no file content verification)"


def validate_metadata_only(
    claimed_duration: float,
    claimed_hotkey: str,
) -> Tuple[bool, str]:
    """
    Weakest validation tier: duration range and hotkey presence only.

    Use only in tests and mock paths where no URL or file is available.
    The live validator intake path should use validate_submission_for_intake()
    which additionally verifies URL scheme and hash shape.
    """
    if claimed_duration < MIN_DURATION_SECS or claimed_duration > MAX_DURATION_SECS:
        return False, f"Duration {claimed_duration}s out of range [{MIN_DURATION_SECS}, {MAX_DURATION_SECS}]"
    if not claimed_hotkey or len(claimed_hotkey) < 10:
        return False, "Invalid or missing hotkey"
    return True, "OK (metadata only — no file validation)"


def _run_probe(path: Path) -> dict:
    """Run FFprobe on the file and return parsed JSON output."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _parse_fps(r_frame_rate: str) -> float:
    """Parse '24000/1001' or '24/1' style frame rate strings."""
    try:
        if "/" in r_frame_rate:
            num_s, den_s = r_frame_rate.split("/", 1)
            num, den = int(num_s), int(den_s)
            return float(num) / float(den) if den != 0 else 0.0
        else:
            return float(r_frame_rate)
    except (ValueError, ZeroDivisionError):
        return 0.0
