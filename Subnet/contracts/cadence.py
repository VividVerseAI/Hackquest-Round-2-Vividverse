"""
vividverse/contracts/cadence.py

Subnet-owner controlled cadence constants.

Canonical production values live in subnet_settings.json (same directory). Python and
the platform both read that file so a single edit to subnet_settings.json updates both
validator and UI for production releases.

For testing/development, individual constants can be overridden with env vars
without touching subnet_settings.json (which should always hold production values):

  SUBNET_SETTING_SUBMISSION_WINDOW_SEC      — override submissionWindowSec
  SUBNET_SETTING_EVALUATION_WINDOW_SEC      — override evaluationWindowSec
  SUBNET_SETTING_PROMPT_VOTING_WINDOW_SEC   — override promptVotingWindowSec
  SUBNET_SETTING_MINER_COUNT_FOR_COUNTDOWN  — override minerCountForCountdown
  SUBNET_SETTING_MIN_VOTED_MINERS_FOR_COUNTDOWN — override minVotedMinersForCountdown
  SUBNET_SETTING_PHASE_TRANSITION_QUORUM    — override phaseTransitionQuorum
  SUBNET_SETTING_FINALISATION_QUORUM        — override finalisationQuorum

Example: start the validator with SUBNET_SETTING_MINER_COUNT_FOR_COUNTDOWN=1 to skip the
miner-count gate during local testing without modifying subnet_settings.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_SETTINGS_FILE = Path(__file__).with_name("subnet_settings.json")


def _int_env(name: str, fallback: int) -> int:
    """Return env var as int when set and valid; otherwise return fallback."""
    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            pass
    return fallback


def _load() -> None:
    """Read subnet_settings.json and env overrides, update module globals in-place.

    Called once at import and again by reload() on each validator step so that
    edits to subnet_settings.json take effect without restarting the validator process.
    """
    global SUBMISSION_WINDOW_SEC, EVALUATION_WINDOW_SEC, PROMPT_VOTING_WINDOW_SEC
    global MINER_COUNT_FOR_COUNTDOWN, MIN_VOTED_MINERS_FOR_COUNTDOWN
    global PHASE_TRANSITION_QUORUM, FINALISATION_QUORUM
    cfg = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
    SUBMISSION_WINDOW_SEC = _int_env("SUBNET_SETTING_SUBMISSION_WINDOW_SEC", int(cfg["submissionWindowSec"]))
    EVALUATION_WINDOW_SEC = _int_env("SUBNET_SETTING_EVALUATION_WINDOW_SEC", int(cfg["evaluationWindowSec"]))
    PROMPT_VOTING_WINDOW_SEC = _int_env("SUBNET_SETTING_PROMPT_VOTING_WINDOW_SEC", int(cfg["promptVotingWindowSec"]))
    MINER_COUNT_FOR_COUNTDOWN = _int_env("SUBNET_SETTING_MINER_COUNT_FOR_COUNTDOWN", int(cfg["minerCountForCountdown"]))
    MIN_VOTED_MINERS_FOR_COUNTDOWN = _int_env("SUBNET_SETTING_MIN_VOTED_MINERS_FOR_COUNTDOWN", int(cfg["minVotedMinersForCountdown"]))
    PHASE_TRANSITION_QUORUM = _int_env("SUBNET_SETTING_PHASE_TRANSITION_QUORUM", int(cfg.get("phaseTransitionQuorum", 1)))
    FINALISATION_QUORUM = _int_env("SUBNET_SETTING_FINALISATION_QUORUM", int(cfg.get("finalisationQuorum", 1)))


def reload() -> None:
    """Reload cadence values from subnet_settings.json and env vars.

    Call at the top of each validator step loop iteration. Changes to
    subnet_settings.json or env overrides take effect within one step — no restart
    needed.
    """
    _load()


# Initialise module-level constants on import.
SUBMISSION_WINDOW_SEC: int = 0
EVALUATION_WINDOW_SEC: int = 0
PROMPT_VOTING_WINDOW_SEC: int = 0
MINER_COUNT_FOR_COUNTDOWN: int = 0
MIN_VOTED_MINERS_FOR_COUNTDOWN: int = 0
PHASE_TRANSITION_QUORUM: int = 1
FINALISATION_QUORUM: int = 1
_load()
