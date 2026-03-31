"""
Additive validator event records — audit trail of validator-produced decisions.

Events are not lifecycle commands: they document what the validator already decided.
When PLATFORM_API_URL is set, events POST to the Platform bridge (same auth as lifecycle).

See: POST /api/validator/events on the Platform.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from vividverse.validator.platform_fetch import push_validator_event_to_platform

# Event type strings (stable for consumers)
EVENT_PROMPT_VOTING_COMPLETE = "prompt_voting_complete"
EVENT_ROUND_BOOTSTRAP = "round_bootstrap"
EVENT_SUBMISSION_WINDOW_CLOSED = "submission_window_closed"
EVENT_EVALUATION_STARTED = "evaluation_started"
EVENT_FINALISATION = "finalisation"
EVENT_RESTART = "restart"


@dataclass(frozen=True)
class ValidatorEvent:
    """Minimal additive record: validator decision log, not an authority request."""

    event_type: str
    timestamp: int
    round_id: Optional[Union[int, str]]
    validator_hotkey: str
    payload: Dict[str, Any]

    def to_bridge_body(self) -> Dict[str, Any]:
        """JSON body for POST /api/validator/events (camelCase for Platform)."""
        return {
            "eventType": self.event_type,
            "timestamp": self.timestamp,
            "roundId": self.round_id,
            "validatorHotkey": self.validator_hotkey,
            "payload": self.payload,
        }


def build_validator_event(
    validator_hotkey: str,
    event_type: str,
    round_id: Optional[Union[int, str]],
    payload: Dict[str, Any],
    *,
    timestamp: Optional[int] = None,
) -> ValidatorEvent:
    """Construct an event; timestamp defaults to wall-clock seconds."""
    return ValidatorEvent(
        event_type=event_type,
        timestamp=int(timestamp if timestamp is not None else time.time()),
        round_id=round_id,
        validator_hotkey=validator_hotkey,
        payload=dict(payload),
    )


def emit_validator_event(
    platform_api_url: str,
    validator_hotkey: str,
    event_type: str,
    round_id: Optional[Union[int, str]],
    payload: Dict[str, Any],
) -> None:
    """
    Best-effort emit: no-op without platform URL; failures are non-fatal (logged in fetch layer).
    """
    if not platform_api_url:
        return
    ev = build_validator_event(validator_hotkey, event_type, round_id, payload)
    rid_int: Optional[int] = None
    if round_id is not None:
        try:
            rid_int = int(round_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            rid_int = None
    push_validator_event_to_platform(platform_api_url, ev.to_bridge_body(), round_id=rid_int)
