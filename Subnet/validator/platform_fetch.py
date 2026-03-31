"""
vividverse/validator/platform_fetch.py

Platform Fetch Helper for Validator

Fetches round state and critic scores from Platform API for subnet integration.
Pushes validator lifecycle state to platform — validator produces, platform consumes.
See docs/NEXT_PHASE_VALIDATOR_AUTHORITY.md.

RETRIES: Transient failures use bounded exponential backoff via vividverse.utils.http_retry
(PLATFORM_HTTP_RETRY_* env). Safe exit returns None/False when exhausted — validator
retries on the next loop iteration without blocking indefinitely.
"""

from __future__ import annotations
import math
import os
import time
import requests
from typing import Dict, Any, Optional, Tuple, List

import bittensor as bt

from vividverse.utils.http_retry import retry_platform_http
from vividverse.validator.lifecycle_emit_cursor import (
    allocate_lifecycle_emit,
    commit_lifecycle_emit,
)
from vividverse.validator.lifecycle_push_result import LifecyclePushResult

# Configurable timeout (seconds). Platform /api/rounds/current can be slow (buildSubnetState, metagraph).
# Default 60s — local dev often exceeds 20s when the validator competes with the UI for Next.js.
DEFAULT_PLATFORM_TIMEOUT = int(os.environ.get("PLATFORM_API_TIMEOUT", "60"))

# Canonical set of valid phase strings in the platform/mechanism.
_VALID_PHASES = frozenset({"prompt_voting", "submission", "evaluation", "finalised"})


def _validate_score_map(
    raw: Any,
    context: str,
) -> Tuple[Dict[str, float], List[str]]:
    """
    Validate a {hotkey: score} mapping from the platform.

    Returns (valid_scores, skipped_reasons). Values must be numeric, finite,
    and non-negative. Malformed entries are skipped with a reason logged by
    the caller; an entirely non-dict input is signalled via a single reason entry
    and an empty valid map.
    """
    if not isinstance(raw, dict):
        return {}, [f"{context}: expected dict, got {type(raw).__name__}"]
    valid: Dict[str, float] = {}
    skipped: List[str] = []
    for k, v in raw.items():
        if not isinstance(k, str) or not k:
            skipped.append(f"{context}: non-string key {k!r} skipped")
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            skipped.append(f"{context}: non-numeric score for {k!r}: {v!r}")
            continue
        if not math.isfinite(fv):
            skipped.append(f"{context}: non-finite score for {k!r}: {fv}")
            continue
        if fv < 0:
            skipped.append(f"{context}: negative score for {k!r}: {fv}")
            continue
        valid[k] = fv
    return valid, skipped


def _normalize_platform_url(url: str) -> str:
    """Ensure platform URL includes /api for Platform API routes."""
    url = (url or "").rstrip("/")
    if url and not url.endswith("/api"):
        url = f"{url}/api"
    return url


def fetch_round_state(
    platform_api_url: str,
    round_id: int,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch round state from Platform subnet API.

    Args:
        platform_api_url: Base URL for Platform API (e.g., http://localhost:3000)
        round_id: Round ID to query
        timeout: Request timeout in seconds

    Returns:
        Dict with round_id, round_phase, submission_deadline_unix,
        evaluation_deadline_unix, narrative_summary, etc.
        None if fetch fails after retries.
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/subnet/rounds/{round_id}/state"
            response = requests.get(url, timeout=t)
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    return False, None, (
                        f"round_state round={round_id}: expected dict, got {type(data).__name__}"
                    )
                phase = data.get("phase") or data.get("round_phase")
                if phase is not None and phase not in _VALID_PHASES:
                    return False, None, (
                        f"round_state round={round_id}: invalid phase {phase!r}"
                    )
                return True, data, ""
            return (
                False,
                None,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("fetch_round_state", round_id, _once)


def fetch_platform_scores(
    platform_api_url: str,
    round_id: int,
    validator_hotkey: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, float]]:
    """
    Fetch aggregated critic scores from Platform subnet API.

    Returns { minerHotkey: rawScore } — mirrors Platform critic flow.
    Mechanism validator maps hotkey -> UID and computes weights.

    When validator_hotkey is provided, the platform returns per-validator scores
    (median of that validator's critic pool per submission) rather than the global aggregate.
    """
    result = fetch_platform_scores_extended(
        platform_api_url, round_id, validator_hotkey=validator_hotkey, timeout=timeout
    )
    return result[0] if result else None


def fetch_majority_status(
    platform_api_url: str,
    round_id: int,
    winner_hotkey: str,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch majority-voted-final status for a round and winner.

    Validator-owned: platform exposes data; validator fetches and decides.
    Uses same retry policy as other Platform GETs (finalisation path).
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/subnet/rounds/{round_id}/majority-status"
            params = {"winnerHotkey": winner_hotkey}
            response = requests.get(url, params=params, timeout=t)
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    return False, None, (
                        f"majority_status round={round_id}: expected dict, got {type(data).__name__}"
                    )
                return True, data, ""
            return (
                False,
                None,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("fetch_majority_status", round_id, _once)


def fetch_critic_quorum_status(
    platform_api_url: str,
    round_id: int,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch distinct critic scorer count vs platform minimum (CRITIC_QUORUM_MIN / critic-status API).

    Used before evaluation→finalised lifecycle push; must align with Platform
    executeValidatorFinalisation gates.
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validation/critic-status"
            response = requests.get(url, params={"roundId": round_id}, timeout=t)
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    return False, None, (
                        f"critic_quorum_status round={round_id}: expected dict, got {type(data).__name__}"
                    )
                for field in ("criticCount", "quorumMin"):
                    raw_val = data.get(field)
                    if raw_val is not None:
                        try:
                            iv = int(raw_val)
                        except (TypeError, ValueError):
                            return False, None, (
                                f"critic_quorum_status round={round_id}: {field} is not an integer: {raw_val!r}"
                            )
                        if iv < 0:
                            return False, None, (
                                f"critic_quorum_status round={round_id}: {field} must be non-negative, got {iv}"
                            )
                return True, data, ""
            return (
                False,
                None,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("fetch_critic_quorum_status", round_id, _once)


def fetch_validator_sync_status(
    platform_api_url: str,
    validator_hotkey: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch validator sync status from platform — use on startup to understand current
    network state before participating in lifecycle consensus.

    Returns a dict with:
      currentState:     phase, roundId, frozen, validatorOnline, deadlines
      activeValidators: count, quorumRequired, validators list (hotkey, phase, lastSeenSecondsAgo)
      pendingProposals: in-flight phase transition votes
      joiningValidator: alignment check + human-readable instruction (when validator_hotkey provided)
      summary:          one-line human-readable state string

    Returns None if fetch fails (validator should log warning and proceed conservatively).
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validator/sync-status"
            params: Dict[str, str] = {}
            if validator_hotkey:
                params["hotkey"] = validator_hotkey
            response = requests.get(url, params=params, timeout=t)
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    return False, None, (
                        f"sync_status: expected dict, got {type(data).__name__}"
                    )
                return True, data, ""
            return (
                False,
                None,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("fetch_validator_sync_status", None, _once)


def fetch_platform_scores_extended(
    platform_api_url: str,
    round_id: int,
    validator_hotkey: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Optional[Tuple[Dict[str, float], Dict[str, int], Dict[str, Any]]]:
    """
    Fetch scores and narrative progression per miner from Platform subnet API.

    Returns (scores, narrative_progression_by_hotkey, meta) where meta contains:
      - allScored: bool — True when every submission has a rawScore (critic aggregation complete)
      - totalSubmissions: int — total submissions in this round (scored + unscored)
      - scoredCount: int — submissions with rawScore present
      - scoreMode: str — "per_validator" when validator_hotkey was recognised; "global_aggregate" otherwise

    When validator_hotkey is provided, the platform returns per-validator scores
    (median of that validator's own critic pool per submission) so each validator submits
    an independent opinion to Yuma, rather than all validators submitting identical global aggregates.
    Falls back to global aggregate for any submission not covered by that validator's critics.
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[
        bool, Optional[Tuple[Dict[str, float], Dict[str, int], Dict[str, Any]]], str
    ]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/subnet/rounds/{round_id}/scores"
            params: Dict[str, Any] = {}
            if validator_hotkey:
                params["validatorHotkey"] = validator_hotkey
            response = requests.get(url, params=params if params else None, timeout=t)
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    return False, None, (
                        f"scores round={round_id}: expected dict response, got {type(data).__name__}"
                    )
                scores_raw = data.get("scores", {})
                if not isinstance(scores_raw, dict):
                    return False, None, (
                        f"scores round={round_id}: 'scores' field is not a dict, got {type(scores_raw).__name__}"
                    )
                scores, skipped = _validate_score_map(scores_raw, f"round {round_id} scores")
                if skipped:
                    preview = "; ".join(skipped[:5])
                    suffix = f" … (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
                    bt.logging.warning(
                        f"Platform scores (round {round_id}): {len(skipped)} entries skipped — {preview}{suffix}"
                    )
                narrative_raw = data.get("narrativeProgressionByHotkey") or {}
                narrative: Dict[str, int] = {}
                for k, v in narrative_raw.items():
                    try:
                        n = int(v)
                        if 0 <= n <= 100:
                            narrative[k] = n
                    except (TypeError, ValueError):
                        pass
                # Completeness metadata — present when the API has been updated; default to
                # "unknown" (all-scored assumed True) so old deployments remain unblocked.
                total_submissions = data.get("totalSubmissions")
                scored_count = data.get("scoredCount")
                all_scored_raw = data.get("allScored")
                if all_scored_raw is None:
                    # API pre-dates completeness fields — fall back to treating scored == total.
                    all_scored = True
                    total_val = len(scores)
                    scored_val = len(scores)
                else:
                    # Explicit bool coercion: never use plain bool() on arbitrary values.
                    # bool("false") is True in Python (non-empty string), which would silently
                    # skip the all-scored gate and allow premature finalisation.
                    if isinstance(all_scored_raw, bool):
                        all_scored = all_scored_raw
                    elif isinstance(all_scored_raw, str):
                        all_scored = all_scored_raw.lower() == "true"
                    elif isinstance(all_scored_raw, (int, float)):
                        all_scored = all_scored_raw != 0
                    else:
                        # Unknown type — treat as unscored to be conservative.
                        bt.logging.warning(
                            f"fetch_platform_scores_extended: unexpected type for allScored "
                            f"({type(all_scored_raw).__name__!r}) — defaulting to False (conservative)."
                        )
                        all_scored = False
                    try:
                        total_val = int(total_submissions) if total_submissions is not None else len(scores)
                        if total_val < 0:
                            raise ValueError("totalSubmissions is negative")
                    except (TypeError, ValueError) as e:
                        bt.logging.warning(
                            f"fetch_platform_scores_extended: could not parse totalSubmissions "
                            f"({total_submissions!r}): {e} — falling back to scored count."
                        )
                        total_val = len(scores)
                    try:
                        scored_val = int(scored_count) if scored_count is not None else len(scores)
                        if scored_val < 0:
                            raise ValueError("scoredCount is negative")
                    except (TypeError, ValueError) as e:
                        bt.logging.warning(
                            f"fetch_platform_scores_extended: could not parse scoredCount "
                            f"({scored_count!r}): {e} — falling back to scored count."
                        )
                        scored_val = len(scores)
                score_mode = str(data.get("scoreMode", "global_aggregate"))
                meta: Dict[str, Any] = {
                    "allScored": all_scored,
                    "totalSubmissions": total_val,
                    "scoredCount": scored_val,
                    "scoreMode": score_mode,
                }
                import json as _json
                bt.logging.info(
                    "[vv_evidence] "
                    + _json.dumps(
                        {
                            "event": "platform_scores_fetched",
                            "round_id": round_id,
                            "score_mode": score_mode,
                            "validator_hotkey": validator_hotkey,
                            "scored_count": scored_val,
                            "total_submissions": total_val,
                            "all_scored": all_scored,
                        }
                    )
                )
                return True, (scores, narrative, meta), ""
            return (
                False,
                None,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("fetch_platform_scores_extended", round_id, _once)


def fetch_prompt_votes(
    platform_api_url: str,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch raw prompt voting data for validator-owned selection.
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/subnet/prompt-votes"
            response = requests.get(url, timeout=t)
            if response.status_code == 200:
                return True, response.json(), ""
            return (
                False,
                None,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except Exception as e:
            return False, None, str(e)

    # round_id N/A for prompt-votes aggregate endpoint
    return retry_platform_http("fetch_prompt_votes", None, _once)


def fetch_subnet_state_summary(
    platform_api_url: str,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    GET /api/subnet/state — same canonical lifecycle payload the UI uses.

    Used when GET /api/rounds/current returns roundId=null (transient build/cache skew)
    but the platform still has an active submission/evaluation round. Aligns validator
    routing with buildSubnetState without changing weight/reward authority (read-only).
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/subnet/state"
            response = requests.get(url, timeout=t)
            if response.status_code != 200:
                return (
                    False,
                    None,
                    f"subnet_state http_{response.status_code}: {response.text[:200]}",
                )
            data = response.json()
            if not isinstance(data, dict):
                return False, None, f"subnet_state: expected dict, got {type(data).__name__}"
            return True, data, ""
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("fetch_subnet_state_summary", None, _once)


def fetch_current_round(
    platform_api_url: str,
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch the current round from Platform API.

    Returns roundId, phase, deadlines, etc.
    None if Platform unreachable or non-200 after retries.
    Note: roundId=null in a 200 response is still success (valid empty state).
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    def _once() -> Tuple[bool, Optional[Dict[str, Any]], str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/rounds/current"
            response = requests.get(url, timeout=t)
            if response.status_code == 200:
                data = response.json()
                if not isinstance(data, dict):
                    return False, None, (
                        f"rounds/current: expected dict, got {type(data).__name__}"
                    )
                rid = data.get("roundId")
                if rid is None:
                    bt.logging.debug(
                        "Platform returned roundId=null (subnet/metagraph likely unavailable). "
                        "Ensure Platform has HTTP_BRIDGE_URL, AUTH_MODE=live; Bridge running with HTTP_BRIDGE_MOCK=0."
                    )
                phase = data.get("phase")
                if phase is not None and phase not in _VALID_PHASES:
                    return False, None, (
                        f"rounds/current: invalid phase {phase!r} (expected one of {sorted(_VALID_PHASES)})"
                    )
                return True, data, ""
            return (
                False,
                None,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except requests.exceptions.ConnectionError as e:
            return False, None, f"connection_error: {e}"
        except requests.exceptions.ReadTimeout as e:
            return False, None, f"read_timeout({t}s): {e}"
        except Exception as e:
            return False, None, str(e)

    return retry_platform_http("fetch_current_round", None, _once)


def push_lifecycle_to_platform(
    platform_api_url: str,
    phase: str,
    round_id: Optional[int] = None,
    selected_prompt_id: Optional[str] = None,
    selected_prompt_computed: Optional[bool] = None,
    transition_source: Optional[str] = None,
    validator_completion: Optional[Dict[str, Any]] = None,
    restart_decision: Optional[Dict[str, Any]] = None,
    canonical_narrative_progression: Optional[int] = None,
    scoring_outcome: Optional[Dict[str, Any]] = None,
    prompt_voting_outcome: Optional[Dict[str, Any]] = None,
    round_bootstrap: Optional[Dict[str, Any]] = None,
    prompt_voting_deadline_unix: Optional[int] = None,
    target_epoch_block: Optional[int] = None,
    validator_hotkey: Optional[str] = None,
    timeout: Optional[int] = None,
    db_path: Optional[str] = None,
) -> LifecyclePushResult:
    """
    Push validator lifecycle state to Platform ingestion endpoint.
    Platform consumes; validator produces. Retries on transient failures only.

    Returns a structured LifecyclePushResult (accepted, round_id, phase, snapshot fields, …).
    When platform_api_url is unset/empty, returns accepted=True without HTTP (same as the
    historical bare-bool \"success\" path so local runs without a platform still proceed).
    """
    lifecycle_id, lifecycle_version = allocate_lifecycle_emit(
        db_path, round_id, validator_hotkey
    )
    produced_at_unix = int(time.time())
    src = (
        transition_source
        if transition_source
        in ("validator_produced", "platform_fallback", "scheduler_triggered")
        else "validator_produced"
    )

    if not (platform_api_url or "").strip():
        bt.logging.debug(
            "push_lifecycle_to_platform: no platform_api_url — skipping HTTP, accepted=True "
            "lifecycle_id=%s lifecycle_version=%s",
            lifecycle_id,
            lifecycle_version,
        )
        commit_lifecycle_emit(db_path, round_id, lifecycle_version)
        return LifecyclePushResult(
            accepted=True,
            round_id=round_id,
            phase=phase,
            lifecycle_id=lifecycle_id,
            lifecycle_version=lifecycle_version,
            reason="",
            retryable=False,
            platform_phase_after_commit=None,
            platform_round_id_after_commit=None,
            idempotent=True,
        )

    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    payload: Dict[str, Any] = {
        "phase": phase,
        "roundId": round_id,
        "selectedPromptId": selected_prompt_id,
        "timestamp": produced_at_unix,
        "lifecycleId": lifecycle_id,
        "lifecycleVersion": lifecycle_version,
        "producedAtUnix": produced_at_unix,
        "source": src,
    }
    if selected_prompt_computed is True:
        payload["selectedPromptComputed"] = True
    if transition_source in ("validator_produced", "platform_fallback", "scheduler_triggered"):
        payload["transitionSource"] = transition_source
    if validator_completion is not None:
        payload["validatorCompletion"] = validator_completion
    if canonical_narrative_progression is not None:
        payload["canonicalNarrativeProgression"] = canonical_narrative_progression
    if scoring_outcome is not None:
        payload["scoringOutcome"] = scoring_outcome
    if restart_decision is not None:
        payload["restartDecision"] = restart_decision
    if prompt_voting_outcome is not None:
        payload["promptVotingOutcome"] = prompt_voting_outcome
    if round_bootstrap is not None:
        payload["roundBootstrap"] = round_bootstrap
    if prompt_voting_deadline_unix is not None:
        payload["promptVotingDeadlineUnix"] = int(prompt_voting_deadline_unix)
    if target_epoch_block is not None:
        payload["targetEpochBlock"] = int(target_epoch_block)
    if validator_hotkey:
        payload["validatorHotkey"] = validator_hotkey

    headers = {}
    secret = os.environ.get("VALIDATOR_INGEST_SECRET")
    if secret:
        headers["x-validator-secret"] = secret

    def _once() -> Tuple[bool, LifecyclePushResult, str]:
        try:
            bt.logging.debug(
                f"[lifecycle_push_attempt] round_id={round_id} phase={phase} lifecycle_id={lifecycle_id} lifecycle_version={lifecycle_version}"
            )
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validator/lifecycle"
            response = requests.post(
                url, json=payload, headers=headers or None, timeout=t
            )
            try:
                raw: Any = response.json()
            except Exception:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            if response.status_code not in (200, 201) and not raw.get(
                "error"
            ) and not raw.get("reason"):
                raw = {
                    **raw,
                    "error": f"http_{response.status_code}: {response.text[:200]}",
                }
            result = LifecyclePushResult.from_json(
                raw,
                fallback_phase=phase,
                fallback_round_id=round_id,
                http_status=response.status_code,
            )
            if response.status_code in (200, 201):
                if result.accepted:
                    commit_lifecycle_emit(db_path, round_id, lifecycle_version)
                bt.logging.debug(
                    f"[lifecycle_push_accepted] accepted={result.accepted} round_id={result.round_id} lifecycle_version={result.lifecycle_version} idempotent={result.idempotent}"
                )
                return True, result, ""

            if response.status_code == 202 and result.quorum_pending:
                # Vote recorded — platform is waiting for more validators before reflecting.
                # This is not an error; do not retry. Next loop iteration will re-push naturally.
                commit_lifecycle_emit(db_path, round_id, lifecycle_version)
                bt.logging.info(
                    "[vv_op] lifecycle_push phase=%s round_id=%s "
                    "quorum_pending=True vote_count=%d/%d — "
                    "vote recorded, waiting for %d more validator(s) to agree",
                    phase,
                    round_id,
                    result.vote_count,
                    result.quorum,
                    max(0, result.quorum - result.vote_count),
                )
                return True, result, ""

            if response.status_code == 409 and result.winner_conflict:
                bt.logging.error(
                    "[vv_op] lifecycle_push WINNER_CONFLICT phase=%s round_id=%s "
                    "reason=%s — validators disagree on winner, manual investigation required",
                    phase,
                    round_id,
                    (result.reason or "")[:400],
                )
                return False, result, result.reason or "WINNER_CONFLICT"

            bt.logging.warning(
                f"[lifecycle_push] rejected http={response.status_code} round_id={result.round_id} reason={(result.reason or '')[:400]}"
            )
            return (
                False,
                result,
                result.reason or "lifecycle_push_failed",
            )
        except Exception as e:
            r = LifecyclePushResult(
                accepted=False,
                round_id=round_id,
                phase=phase,
                lifecycle_id=lifecycle_id,
                lifecycle_version=lifecycle_version,
                reason=str(e),
                retryable=True,
                platform_phase_after_commit=None,
                platform_round_id_after_commit=None,
            )
            bt.logging.warning(
                "[lifecycle_push] exception round_id=%s err=%s",
                round_id,
                str(e)[:400],
            )
            return False, r, str(e)

    return retry_platform_http("push_lifecycle_to_platform", round_id, _once)


def push_validator_event_to_platform(
    platform_api_url: str,
    body: Dict[str, Any],
    round_id: Optional[int] = None,
    timeout: Optional[int] = None,
) -> bool:
    """
    POST additive validator audit event to Platform (same auth as lifecycle).

    Endpoint: POST /api/validator/events — append-only record; does not drive lifecycle.
    """
    t = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT

    headers: Dict[str, Any] = {}
    secret = os.environ.get("VALIDATOR_INGEST_SECRET")
    if secret:
        headers["x-validator-secret"] = secret

    def _once() -> Tuple[bool, bool, str]:
        try:
            base = _normalize_platform_url(platform_api_url)
            url = f"{base}/validator/events"
            response = requests.post(
                url, json=body, headers=headers or None, timeout=t
            )
            if response.status_code in (200, 201):
                bt.logging.debug("Validator event POST accepted by Platform")
                return True, True, ""
            return (
                False,
                False,
                f"http_{response.status_code}: {response.text[:200]}",
            )
        except Exception as e:
            return False, False, str(e)

    return retry_platform_http("push_validator_event_to_platform", round_id, _once)
