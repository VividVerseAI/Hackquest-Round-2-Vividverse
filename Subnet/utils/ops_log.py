"""
Structured operational logs for the Vividverse mechanism (validator, HTTP retries).

Format (single line, grep-friendly):
  [vv_op] action=<name> round_id=<n|-> phase=<str|-> validator_hotkey=<ss58|-> reason=<str|->

Missing values use '-'. Reason is always last; keep it single-line (no raw newlines).

End-to-end round evidence (JSON lines): see vividverse.utils.evidence_log ([vv_evidence]).
Readable milestone lines (same trace_key): vividverse.utils.evidence_log.log_vv_proof ([vv_proof]).
"""

from __future__ import annotations

import re
from typing import Optional, Union

import bittensor as bt

_MISSING = "-"


def _fmt(v: Optional[Union[int, str]]) -> str:
    if v is None or v == "":
        return _MISSING
    return str(v)


def _clean_reason(reason: Optional[str]) -> str:
    if reason is None or reason == "":
        return _MISSING
    s = " ".join(reason.replace("\n", " ").split()).strip()
    if not s:
        return _MISSING
    # Avoid log injection / huge lines
    s = re.sub(r"\s+", " ", s)
    return s[:500] if len(s) > 500 else s


def format_vv_op(
    action: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    phase: Optional[str] = None,
    validator_hotkey: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """Build one standardized [vv_op] line with five operational fields."""
    return (
        f"[vv_op] action={_fmt(action)} round_id={_fmt(round_id)} phase={_fmt(phase)} "
        f"validator_hotkey={_fmt(validator_hotkey)} reason={_clean_reason(reason)}"
    )


def log_vv_info(
    action: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    phase: Optional[str] = None,
    validator_hotkey: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    bt.logging.info(
        format_vv_op(
            action,
            round_id=round_id,
            phase=phase,
            validator_hotkey=validator_hotkey,
            reason=reason,
        )
    )


def log_vv_debug(
    action: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    phase: Optional[str] = None,
    validator_hotkey: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    bt.logging.debug(
        format_vv_op(
            action,
            round_id=round_id,
            phase=phase,
            validator_hotkey=validator_hotkey,
            reason=reason,
        )
    )


def log_vv_warning(
    action: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    phase: Optional[str] = None,
    validator_hotkey: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    bt.logging.warning(
        format_vv_op(
            action,
            round_id=round_id,
            phase=phase,
            validator_hotkey=validator_hotkey,
            reason=reason,
        )
    )


def log_vv_success(
    action: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    phase: Optional[str] = None,
    validator_hotkey: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Log a milestone at SUCCESS level — always visible regardless of log filtering."""
    bt.logging.success(
        format_vv_op(
            action,
            round_id=round_id,
            phase=phase,
            validator_hotkey=validator_hotkey,
            reason=reason,
        )
    )


def log_vv_error(
    action: str,
    *,
    round_id: Optional[Union[int, str]] = None,
    phase: Optional[str] = None,
    validator_hotkey: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    bt.logging.error(
        format_vv_op(
            action,
            round_id=round_id,
            phase=phase,
            validator_hotkey=validator_hotkey,
            reason=reason,
        )
    )
