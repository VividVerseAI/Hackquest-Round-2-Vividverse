"""
vividverse/validator/consensus_fetch.py

Platform consensus API — propose, vote, and fetch checkpoint for validator phase transitions.
When CONSENSUS_ENABLED=true, validators must propose transitions and wait for quorum
before advancing. On startup or crash, validators resume from the last checkpoint.

HTTP (consistent with other validator→Platform calls):
  - Every consensus request goes through retry_platform_http — bounded exponential backoff,
    same PLATFORM_HTTP_RETRY_* knobs as vividverse/validator/platform_fetch.py (max attempts,
    initial/max sleep, total sleep budget). Transient failures are retried; exhaustion returns
    None/[] and the validator loop can retry on the next step.
  - Per-attempt socket timeout: default 20s per HTTP call (preserves pre-retry behavior).
    Production tuning: set CONSENSUS_API_TIMEOUT (seconds) to raise or lower the wait for
    slow consensus endpoints (independent of PLATFORM_API_TIMEOUT used by subnet/round fetches).
"""

from __future__ import annotations
import os
import time
import requests
from typing import Optional, Dict, Any, List, Tuple

import bittensor as bt

from vividverse.utils.http_retry import retry_platform_http

# Per-request timeout (seconds). Default 20; set CONSENSUS_API_TIMEOUT to tune.
DEFAULT_CONSENSUS_TIMEOUT = int(os.environ.get("CONSENSUS_API_TIMEOUT", "20"))

# Must match platform/mechanism phase enum.
_VALID_PHASES = frozenset({"prompt_voting", "submission", "evaluation", "finalised"})


def _normalize_platform_url(url: str) -> str:
    """Ensure platform URL includes /api for Platform API routes."""
    url = (url or "").rstrip("/")
    if url and not url.endswith("/api"):
        url = f"{url}/api"
    return url


def _auth_headers() -> Dict[str, str]:
    headers = {}
    secret = os.environ.get("VALIDATOR_INGEST_SECRET")
    if secret:
        headers["x-validator-secret"] = secret
    return headers


def propose_transition(
    platform_api_url: str,
    from_phase: str,
    to_phase: str,
    round_id: Optional[int],
    proposed_by_hotkey: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Propose a phase transition. Returns { proposalId, alreadyExists? } or None on error.
    """
    if from_phase not in _VALID_PHASES:
        bt.logging.warning(f"propose_transition: invalid from_phase {from_phase!r} — rejected")
        return None
    if to_phase not in _VALID_PHASES:
        bt.logging.warning(f"propose_transition: invalid to_phase {to_phase!r} — rejected")
        return None

    t = timeout if timeout is not None else DEFAULT_CONSENSUS_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validator/consensus/propose"
            data: Dict[str, Any] = {
                "fromPhase": from_phase,
                "toPhase": to_phase,
                "roundId": round_id,
                "validatorHotkey": proposed_by_hotkey,
            }
            if payload is not None:
                data["payload"] = payload

            resp = requests.post(
                url,
                json=data,
                headers=_auth_headers(),
                timeout=t,
            )
            if resp.status_code in (200, 201):
                r = resp.json()
                if not isinstance(r, dict):
                    return False, None, (
                        f"propose: expected dict response, got {type(r).__name__}"
                    )
                pid = r.get("proposalId")
                if not isinstance(pid, str) or not pid:
                    return False, None, (
                        f"propose: proposalId missing or not a non-empty string: {pid!r}"
                    )
                return True, r, ""
            return (
                False,
                None,
                f"http_{resp.status_code}: {resp.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("consensus_propose_transition", round_id, _once)


def vote_on_proposal(
    platform_api_url: str,
    proposal_id: str,
    validator_hotkey: str,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Vote on a phase transition proposal.
    Returns { accepted, confirmed? } or None on error.
    """
    t = timeout if timeout is not None else DEFAULT_CONSENSUS_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validator/consensus/vote"
            resp = requests.post(
                url,
                json={
                    "proposalId": proposal_id,
                    "validatorHotkey": validator_hotkey,
                },
                headers=_auth_headers(),
                timeout=t,
            )
            if resp.status_code in (200, 201):
                r = resp.json()
                if not isinstance(r, dict):
                    return False, None, (
                        f"vote: expected dict response, got {type(r).__name__}"
                    )
                if "accepted" not in r:
                    return False, None, "vote: response missing 'accepted' field"
                return True, r, ""
            return (
                False,
                None,
                f"http_{resp.status_code}: {resp.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("consensus_vote_on_proposal", None, _once)


def get_last_checkpoint(
    platform_api_url: str,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch the last confirmed checkpoint for crash recovery.
    Returns { checkpoint: { phase, roundId, selectedPromptId, payload, ... } } or { checkpoint: null }.
    """
    t = timeout if timeout is not None else DEFAULT_CONSENSUS_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validator/consensus/checkpoint"
            resp = requests.get(
                url,
                headers=_auth_headers(),
                timeout=t,
            )
            if resp.status_code == 200:
                r = resp.json()
                if not isinstance(r, dict):
                    return False, None, (
                        f"checkpoint: expected dict response, got {type(r).__name__}"
                    )
                cp = r.get("checkpoint")
                if cp is not None and not isinstance(cp, dict):
                    return False, None, (
                        f"checkpoint: 'checkpoint' field is not a dict: {type(cp).__name__}"
                    )
                if isinstance(cp, dict):
                    phase = cp.get("phase")
                    if phase is not None and phase not in _VALID_PHASES:
                        return False, None, (
                            f"checkpoint: invalid phase {phase!r}"
                        )
                return True, r, ""
            return (
                False,
                None,
                f"http_{resp.status_code}: {resp.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("consensus_get_last_checkpoint", None, _once)


def get_pending_proposals(
    platform_api_url: str,
    timeout: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch pending phase transition proposals.
    Returns list of proposals with id, fromPhase, toPhase, voteCount, quorumRequired.
    """
    t = timeout if timeout is not None else DEFAULT_CONSENSUS_TIMEOUT

    def _once() -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validator/consensus/proposals"
            resp = requests.get(
                url,
                headers=_auth_headers(),
                timeout=t,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, data.get("proposals", []), ""
            return (
                False,
                None,
                f"http_{resp.status_code}: {resp.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    out = retry_platform_http("consensus_get_pending_proposals", None, _once)
    return out if isinstance(out, list) else []


def _verify_checkpoint_applied(
    platform_api_url: str,
    to_phase: str,
    round_id: Optional[int],
    timeout: int,
) -> bool:
    """
    Verify via checkpoint read that the transition to `to_phase` was actually applied.

    Returns True only when the checkpoint confirms the expected phase (and round when
    known). A False result means the checkpoint is absent, malformed, or disagrees —
    do not treat a False as definitive failure; use for logging/gating decisions.
    """
    try:
        cp_result = get_last_checkpoint(platform_api_url, timeout=timeout)
        if not cp_result or not isinstance(cp_result, dict):
            return False
        cp = cp_result.get("checkpoint")
        if not isinstance(cp, dict):
            return False
        if cp.get("phase") != to_phase:
            return False
        if round_id is not None:
            cp_round = cp.get("roundId")
            if cp_round is not None:
                try:
                    if int(cp_round) != round_id:
                        return False
                except (TypeError, ValueError):
                    return False
        return True
    except Exception:
        return False


def sync_from_checkpoint_and_propose_or_vote(
    platform_api_url: str,
    validator_hotkey: str,
    from_phase: str,
    to_phase: str,
    round_id: Optional[int],
    payload: Optional[Dict[str, Any]],
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    timeout: int = 20,
) -> Tuple[bool, str]:
    """
    Ensure transition is confirmed via consensus:
    1. Propose the transition (or find existing proposal)
    2. Vote on it
    3. Poll until quorum is reached or timeout
    4. If confirmed, Platform has already applied it; return (True, "")

    Returns (confirmed, reason). reason is empty on success; on failure use reason for logging.
    """
    # Propose (or get existing)
    propose_result = propose_transition(
        platform_api_url,
        from_phase,
        to_phase,
        round_id,
        validator_hotkey,
        payload,
        timeout=timeout,
    )
    if not propose_result:
        return False, "propose_failed"

    proposal_id = propose_result.get("proposalId")
    if not proposal_id:
        return False, "no_proposal_id"

    # Vote
    vote_result = vote_on_proposal(
        platform_api_url,
        proposal_id,
        validator_hotkey,
        timeout=timeout,
    )
    if not vote_result or not vote_result.get("accepted"):
        error_msg = (vote_result or {}).get("error", "")
        if "already confirmed" in error_msg.lower():
            # Other validator reached quorum first — proposal is confirmed; treat as success.
            return True, ""
        return False, "vote_rejected"

    if vote_result.get("confirmed"):
        # Immediate server confirmation — verify checkpoint; a timing lag is tolerated.
        if not _verify_checkpoint_applied(platform_api_url, to_phase, round_id, timeout=timeout):
            bt.logging.warning(
                f"Consensus: vote confirmed for {from_phase}→{to_phase} (round {round_id}) "
                "but checkpoint does not yet reflect it — timing lag; trusting vote response"
            )
        return True, ""

    # Poll until confirmed or timeout.
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        time.sleep(poll_interval_seconds)
        proposals = get_pending_proposals(platform_api_url, timeout=timeout)
        our_proposal = next(
            (p for p in proposals if p.get("id") == proposal_id),
            None,
        )
        if not our_proposal:
            # Proposal removed from pending — must verify via checkpoint before declaring success.
            # Do NOT treat disappearance as implicit confirmation.
            if _verify_checkpoint_applied(platform_api_url, to_phase, round_id, timeout=timeout):
                return True, ""
            bt.logging.warning(
                f"Consensus: proposal {proposal_id} disappeared from pending but checkpoint "
                f"does not confirm {from_phase}→{to_phase} (round {round_id}) — unconfirmed"
            )
            return False, "disappeared_unconfirmed"
        if our_proposal.get("voteCount", 0) >= our_proposal.get("quorumRequired", 1):
            # Vote count at quorum — verify checkpoint before returning success.
            if _verify_checkpoint_applied(platform_api_url, to_phase, round_id, timeout=timeout):
                return True, ""
            # Checkpoint not yet updated — keep polling; platform may still be applying.
            bt.logging.debug(
                f"Consensus: vote count at quorum for {from_phase}→{to_phase} (round {round_id}) "
                "but checkpoint not yet updated — continuing poll"
            )

    bt.logging.warning(
        f"Consensus timeout for {from_phase}→{to_phase} (round {round_id})"
    )
    return False, "quorum_timeout"
