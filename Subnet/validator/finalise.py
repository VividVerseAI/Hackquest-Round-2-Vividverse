"""
vividverse/validator/finalise.py

Platform finalisation logic — extracted from Validator._finalise_round_platform.

Entry point: ``await finalise_round_platform(v, round_mgr)``

Steps (in order):
  1. Prefetch fresh round state (guards against stale step snapshot)
  2. Critic quorum gate
  3. Bittensor tempo boundary gate
  4. Idempotency check (local finalisation marker)
  5. Fetch + validate platform scores
  6. Map hotkey → UID; build weights
  7. Consensus transition (evaluation → finalised)
  8. set_weights on chain
  9. Push finalised lifecycle to platform
 10. Write local idempotency marker + emit evidence
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

import bittensor as bt
import torch

import vividverse.contracts.cadence as _cadence
from vividverse.contracts.tempo import compute_next_epoch_block, is_tempo_complete
from vividverse.utils.finalisation_marker import (
    is_round_finalised,
    mark_round_finalised,
    remove_stale_marker_if_platform_phase_active,
)
from vividverse.utils.ops_log import log_vv_error, log_vv_info, log_vv_warning
from vividverse.utils.evidence_log import (
    log_evidence,
    log_vv_proof,
    log_vv_judge,
    summarize_weights_for_evidence,
)
from vividverse.utils.last_round_summary import maybe_dump_last_round_summary
from vividverse.utils.platform_round import deadline_only_round_bootstrap, get_round_id
from vividverse.validator.platform_fetch import (
    fetch_critic_quorum_status,
    fetch_majority_status,
    fetch_platform_scores_extended,
    fetch_round_state,
)
from vividverse.validator.reward import QUALITY_THRESHOLD, compute_weights, identify_winner
from vividverse.contracts.round_registry import compute_round_deadlines
from vividverse.validator.events import EVENT_FINALISATION, EVENT_RESTART

if TYPE_CHECKING:
    from neurons.validator import Validator


async def finalise_round_platform(v: "Validator", round_mgr: Dict[str, Any]) -> None:
    """
    Platform mode: fetch scores, compute weights, set_weights on chain, push finalised lifecycle.

    All precondition gates (critic quorum, tempo, idempotency) are checked in order.
    Nothing irreversible fires until every gate passes.
    """
    round_id = get_round_id(round_mgr)

    # ── 1. Fresh round state ──────────────────────────────────────────────────
    bt.logging.info(
        f"[platform_read] fetch_round_state source=fresh purpose=finalise_precondition round_id={round_id}"
    )
    fresh_state = fetch_round_state(v.platform_api_url, round_id)
    if not fresh_state:
        bt.logging.warning(
            f"[platform_read] fetch_round_state failed round_id={round_id} — skip finalisation "
            "(retry next loop)"
        )
        log_vv_warning(
            "finalisation_skipped",
            round_id=round_id, phase="-",
            validator_hotkey=v._validator_hotkey(),
            reason="fetch_round_state_failed retry_next_loop",
        )
        v._liveness_note("platform_finalise_prefetch_failed", round_id=round_id, phase="evaluation")
        v._platform_round_cache = None
        return

    rp = fresh_state.get("round_phase", "")
    if rp not in ("evaluation", "finalised"):
        bt.logging.warning(
            f"[platform_read] round {round_id} phase={rp!r} — not finalising "
            "(expected evaluation/finalised)"
        )
        log_vv_warning(
            "finalisation_skipped",
            round_id=round_id, phase=str(rp),
            validator_hotkey=v._validator_hotkey(),
            reason="unexpected_phase expected_evaluation_or_finalised",
        )
        v._liveness_note("platform_finalise_wrong_phase", round_id=round_id, phase=str(rp))
        return
    round_mgr = fresh_state

    # ── 2. Critic quorum gate ────────────────────────────────────────────────
    bt.logging.info(
        f"[platform_read] fetch_critic_quorum_status source=fresh purpose=finalise_precondition "
        f"round_id={round_id}"
    )
    cq_data = fetch_critic_quorum_status(v.platform_api_url, round_id)
    if cq_data is None:
        bt.logging.warning(
            f"Round {round_id} finalisation skipped — critic quorum status fetch failed "
            "(retry next loop)"
        )
        log_vv_warning(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason="fetch_critic_quorum_status_failed retry_next_loop",
        )
        v._liveness_note("platform_finalise_critic_quorum_fetch_failed", round_id=round_id, phase="evaluation")
        v._platform_round_cache = None
        return

    _d = cq_data.get("distinctCriticsScored")
    _m = cq_data.get("criticQuorumMin")
    cq_distinct = "?" if _d is None else str(_d)
    cq_min = "?" if _m is None else str(_m)
    cq_met = bool(cq_data.get("criticQuorumMet", False))
    bt.logging.info(
        f"Round {round_id} critic quorum: {cq_distinct}/{cq_min} distinct critics scored — "
        f"minimum {'reached' if cq_met else 'not reached yet'}"
    )
    if cq_met:
        bt.logging.info(
            "Critic quorum satisfied; finalisation still waits for the Bittensor tempo epoch "
            "countdown (blocks_since_last_step==0)."
        )

    if not cq_met:
        bt.logging.info(
            f"Round {round_id} finalisation skipped — critic quorum not met: "
            f"{cq_distinct}/{cq_min} distinct critics scored"
        )
        log_vv_info(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason="critic_quorum_not_met_on_platform",
        )
        v._liveness_note("platform_finalise_critic_quorum_wait", round_id=round_id, phase="evaluation")
        v._platform_round_cache = None
        return

    # ── 3. Tempo gate ────────────────────────────────────────────────────────
    v.metagraph.sync(subtensor=v.subtensor)

    if v._tempo_wait_round_id != round_id:
        v._tempo_wait_round_id = round_id
        v._tempo_wait_target_block = compute_next_epoch_block(
            v.metagraph, v.subtensor, v.config.netuid
        )
        if v._tempo_wait_target_block is not None:
            bt.logging.info(
                f"Round {round_id}: epoch-deadline fallback set — will finalise at block "
                f"{v._tempo_wait_target_block} if blocks_since_last_step==0 is never observed."
            )

    complete, reason = is_tempo_complete(
        v.metagraph, v.subtensor, v.config.netuid,
        deadline_block=v._tempo_wait_target_block,
    )
    if not complete:
        bt.logging.info(
            f"Round {round_id} finalisation deferred — tempo step boundary not reached: {reason}. "
            f"Critic quorum satisfied ({cq_distinct}/{cq_min}); polling until tempo completes."
        )
        log_vv_info(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason=f"tempo_incomplete {reason}",
        )
        v._liveness.mark_tempo_wait(round_id=round_id, phase="evaluation", reason=str(reason or ""))
        v._liveness_note("healthy_wait:tempo", round_id, "evaluation")
        v._in_tempo_wait = True
        return
    v._in_tempo_wait = False

    # ── 4. Idempotency check ─────────────────────────────────────────────────
    phase_low = str(round_mgr.get("round_phase", "")).lower()
    if is_round_finalised(round_id, v.db_path):
        if phase_low == "evaluation":
            if remove_stale_marker_if_platform_phase_active(round_id, phase_low, v.db_path):
                bt.logging.warning(
                    json.dumps({
                        "event": "finalisation_marker_removed_due_to_mismatch",
                        "round_id": round_id,
                        "platform_round_phase": phase_low,
                        "source": "platform_finalise_path",
                    })
                )
                log_vv_warning(
                    "finalisation_marker_removed_due_to_mismatch",
                    round_id=round_id, phase=phase_low,
                    validator_hotkey=v._validator_hotkey(),
                    reason="platform_evaluation_vs_local_marker cleared_for_retry",
                )
            # cleared or inconsistent — proceed to finalisation
        elif phase_low == "finalised":
            bt.logging.info(
                f"Round {round_id} finalisation skipped — already finalised "
                "(idempotent, marker found; platform round_phase=finalised)"
            )
            log_vv_info(
                "finalisation_marker_skipped",
                round_id=round_id, phase="evaluation",
                validator_hotkey=v._validator_hotkey(),
                reason="idempotent platform_finalised marker_present skip_duplicate_set_weights",
            )
            v._liveness_note("idempotent_skip_finalisation", round_id=round_id, phase="evaluation")
            v._platform_round_cache = None
            return

    # ── 5. Fetch + validate scores ───────────────────────────────────────────
    log_vv_info(
        "finalisation_started",
        round_id=round_id, phase="evaluation",
        validator_hotkey=v._validator_hotkey(),
        reason="tempo_complete fetching_platform_scores",
    )
    bt.logging.info(f"Tempo completed — proceeding with round {round_id} finalisation")

    platform_result = fetch_platform_scores_extended(
        v.platform_api_url, round_id, validator_hotkey=v._validator_hotkey()
    )
    if not platform_result:
        bt.logging.warning("No Platform scores — skipping weight setting")
        v._push_lifecycle(
            "evaluation", round_id,
            selected_prompt_id=None,
            round_bootstrap=deadline_only_round_bootstrap(round_mgr),
        )
        v._liveness_note("platform_finalise_no_scores", round_id=round_id, phase="evaluation")
        return

    if not isinstance(platform_result, tuple) or len(platform_result) != 3:
        bt.logging.warning(
            f"[validator] Unexpected platform_result structure "
            f"(type={type(platform_result).__name__}, expected 3-tuple) — skipping finalisation."
        )
        return
    platform_scores, narrative_by_hotkey, scores_meta = platform_result

    # Parse completeness metadata — explicit bool coercion (bool("false") == True in Python).
    _all_scored_raw = scores_meta.get("allScored", True)
    if isinstance(_all_scored_raw, bool):
        all_scored = _all_scored_raw
    elif isinstance(_all_scored_raw, str):
        all_scored = _all_scored_raw.lower() == "true"
    elif isinstance(_all_scored_raw, (int, float)):
        all_scored = _all_scored_raw != 0
    else:
        bt.logging.warning(
            f"[validator] Unexpected type for allScored ({type(_all_scored_raw).__name__!r}) "
            "— treating as False (conservative)."
        )
        all_scored = False

    try:
        total_submissions = int(scores_meta.get("totalSubmissions", len(platform_scores)))
    except (TypeError, ValueError):
        total_submissions = len(platform_scores)
    try:
        scored_count = int(scores_meta.get("scoredCount", len(platform_scores)))
    except (TypeError, ValueError):
        scored_count = len(platform_scores)
    score_mode = str(scores_meta.get("scoreMode", "global_aggregate"))

    bt.logging.info(
        f"[vv_evidence] scores mode={score_mode} round={round_id} "
        f"scored={scored_count}/{total_submissions} all_scored={all_scored} "
        f"hotkey={v._validator_hotkey()}"
    )

    if not all_scored:
        bt.logging.info(
            f"Round {round_id} finalisation deferred — {scored_count}/{total_submissions} scored. "
            "Waiting for all critic evaluations to complete."
        )
        log_vv_info(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason=f"scores_incomplete scored={scored_count} total={total_submissions}",
        )
        v._liveness_note("platform_finalise_scores_incomplete", round_id=round_id, phase="evaluation")
        v._platform_round_cache = None
        return

    # ── 6. Map hotkey → UID; compute winner + weights ────────────────────────
    # Guard: duplicate hotkeys in metagraph would silently overwrite one miner's score.
    all_hotkeys = list(v.metagraph.hotkeys)
    if len(all_hotkeys) != len(set(all_hotkeys)):
        from collections import Counter
        dupes = [h for h, n in Counter(all_hotkeys).items() if n > 1]
        bt.logging.error(
            f"[finalise] Metagraph contains duplicate hotkeys — skipping finalisation. "
            f"Duplicates (sample): {dupes[:5]}. Metagraph may be corrupted or stale."
        )
        log_vv_error(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason="metagraph_duplicate_hotkeys",
        )
        v._platform_round_cache = None
        return
    hotkey_to_uid: Dict[str, int] = {h: uid for uid, h in enumerate(all_hotkeys)}
    scores: Dict[int, float] = {
        hotkey_to_uid[hk]: float(raw)
        for hk, raw in platform_scores.items()
        if hk in hotkey_to_uid
    }

    if not scores:
        if not platform_scores:
            bt.logging.warning(
                "Platform returned no miner scores for this round — skipping weight setting"
            )
        else:
            unmatched = set(platform_scores.keys()) - set(hotkey_to_uid.keys())
            bt.logging.warning(
                "Platform scores could not be mapped to metagraph UIDs — skipping. "
                f"Unmatched hotkeys (sample): {list(unmatched)[:5]}"
            )
        log_evidence(
            "validator", "scoring_evaluation",
            round_id=round_id, phase="evaluation",
            action="platform_hotkey_to_uid", reason="no_scores_after_uid_map",
            uid_to_score={}, valid_scored_miners=0,
            quality_threshold=float(QUALITY_THRESHOLD),
            validator_hotkey=v._validator_hotkey(),
        )
        v._push_lifecycle(
            "evaluation", round_id,
            selected_prompt_id=None,
            round_bootstrap=deadline_only_round_bootstrap(round_mgr),
        )
        v._liveness_note("platform_finalise_no_uid_scores", round_id=round_id, phase="evaluation")
        return

    log_evidence(
        "validator", "scoring_evaluation",
        round_id=round_id, phase="evaluation",
        action="platform_scores_mapped", reason="raw_scores_before_winner",
        uid_to_score={str(k): float(v_) for k, v_ in sorted(scores.items())},
        uid_to_miner_hotkey={str(uid): str(v.metagraph.hotkeys[uid]) for uid in scores},
        valid_scored_miners=len(scores),
        quality_threshold=float(QUALITY_THRESHOLD),
        source="platform_api", validator_hotkey=v._validator_hotkey(),
    )
    log_vv_proof(
        "validator_scoring_inputs_loaded",
        round_id=round_id, validator_hotkey=v._validator_hotkey(),
        source="platform_api", valid_scored_miners=len(scores),
        netuid=int(v.config.netuid),
    )

    # All-below-threshold → restart
    # When running in per_validator score mode, this validator's critics may all be below
    # threshold while another validator's critics scored a miner above it.  Before committing
    # to a restart, cross-check against the global aggregate scores (fetched without a
    # validator hotkey).  Only restart if the global view *also* shows all scores below
    # threshold — i.e. no validator across the whole network has a score >= QUALITY_THRESHOLD.
    if all(s < QUALITY_THRESHOLD for s in scores.values()):
        _should_restart = True
        if score_mode == "per_validator":
            bt.logging.info(
                f"[finalise] Per-validator scores all below threshold ({QUALITY_THRESHOLD}) "
                "— fetching global aggregate to confirm restart decision"
            )
            global_result = fetch_platform_scores_extended(
                v.platform_api_url, round_id, validator_hotkey=None
            )
            if global_result and isinstance(global_result, tuple) and len(global_result) == 3:
                global_scores_raw, _, _global_meta = global_result
                global_scores: Dict[int, float] = {
                    hotkey_to_uid[hk]: float(raw)
                    for hk, raw in global_scores_raw.items()
                    if hk in hotkey_to_uid
                }
                if any(s >= QUALITY_THRESHOLD for s in global_scores.values()):
                    bt.logging.info(
                        f"[finalise] Global aggregate has score(s) >= {QUALITY_THRESHOLD} "
                        "— skipping restart; proceeding with weight-setting using per-validator scores"
                    )
                    log_evidence(
                        "validator", "lifecycle_transition",
                        round_id=round_id, phase="evaluation",
                        action="restart_suppressed_by_global_scores",
                        reason=(
                            f"per_validator_all_below threshold={QUALITY_THRESHOLD} "
                            "but global_aggregate_has_passing_score"
                        ),
                        validator_hotkey=v._validator_hotkey(),
                    )
                    _should_restart = False
                else:
                    bt.logging.info(
                        f"[finalise] Global aggregate also all below threshold ({QUALITY_THRESHOLD}) "
                        "— restart confirmed"
                    )
            else:
                bt.logging.warning(
                    "[finalise] Could not fetch global aggregate scores to confirm restart — "
                    "proceeding with restart based on per-validator scores alone"
                )

        if _should_restart:
            bt.logging.info(f"All scores below threshold ({QUALITY_THRESHOLD}) — pushing restart")
            log_evidence(
                "validator", "lifecycle_transition",
                round_id=round_id, phase="evaluation",
                action="restart_low_scores",
                reason=f"all_below_quality_threshold threshold={QUALITY_THRESHOLD}",
                validator_hotkey=v._validator_hotkey(),
            )
            sub_unix, eval_unix = compute_round_deadlines()
            restart_payload = {
                "restartDecision": {
                    "restarted": True,
                    "reason": "All scores below quality threshold",
                    "submissionDeadlineUnix": sub_unix,
                    "evaluationDeadlineUnix": eval_unix,
                },
                "roundBootstrap": {"submissionDeadlineUnix": sub_unix, "evaluationDeadlineUnix": eval_unix},
            }
            ok, cons_reason = await v._push_transition_via_consensus(
                "evaluation", "submission", round_id, restart_payload
            )
            if ok:
                v._log_consensus_success("evaluation", "submission", round_id)
                log_vv_info(
                    "restart",
                    round_id=round_id, phase="evaluation->submission",
                    validator_hotkey=v._validator_hotkey(),
                    reason=f"all_scores_below_threshold threshold={QUALITY_THRESHOLD} path=consensus",
                )
                v._emit_validator_event(
                    EVENT_RESTART, round_id,
                    {"reason": "all_scores_below_quality_threshold", "path": "consensus",
                     "quality_threshold": QUALITY_THRESHOLD},
                )
            else:
                v._log_consensus_failure("evaluation", "submission", round_id, cons_reason)
                v._liveness_note("consensus_retry_next_loop", round_id=round_id, phase="evaluation")
            v._platform_round_cache = None
            return

    # Pre-validate all score UIDs are within metagraph bounds before selecting winner.
    n_hotkeys = len(all_hotkeys)
    oob = [uid for uid in scores if uid >= n_hotkeys]
    if oob:
        bt.logging.error(
            f"[finalise] Score UIDs out of metagraph bounds (n={n_hotkeys}): {oob[:5]} — "
            "metagraph may be stale. Skipping finalisation."
        )
        log_vv_error(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason=f"score_uids_out_of_bounds n_hotkeys={n_hotkeys} oob_sample={oob[:5]}",
        )
        v._platform_round_cache = None
        return

    # Winner
    winner_uid = identify_winner(scores)
    if winner_uid is None:
        bt.logging.error("identify_winner returned None despite non-empty scores")
        log_vv_error(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason="winner_identification_failed",
        )
        v._liveness_note("platform_finalise_weight_compute_failed", round_id=round_id, phase="evaluation")
        return

    try:
        weights = compute_weights(scores, n_total_uids=v.metagraph.n.item())
    except ValueError as e:
        bt.logging.error(f"Weight computation failed: {e}")
        log_vv_error(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason=f"weight_compute_failed {e}",
        )
        v._liveness_note("platform_finalise_weight_compute_failed", round_id=round_id, phase="evaluation")
        return

    winner_hotkey = v.metagraph.hotkeys[winner_uid]
    aggregated = sum(scores.values())
    uids = torch.arange(v.metagraph.n.item())

    log_evidence(
        "validator", "winner_determination",
        round_id=round_id, phase="evaluation",
        action="identify_winner", reason="max_raw_critic_score_not_weights",
        validator_hotkey=v._validator_hotkey(),
        raw_scores_uid_to_score={str(k): float(v_) for k, v_ in sorted(scores.items())},
        winner_uid=int(winner_uid), winner_hotkey=str(winner_hotkey),
        miner_hotkey=str(winner_hotkey), miner_uid=int(winner_uid),
        winner_raw_score=float(scores.get(winner_uid, 0.0)),
        aggregated_scores_sum=float(aggregated),
    )
    log_vv_proof(
        "validator_winner_fixed_from_raw_scores",
        round_id=round_id, validator_hotkey=v._validator_hotkey(),
        winner_uid=int(winner_uid), winner_hotkey=str(winner_hotkey),
        winner_raw_score=float(scores.get(winner_uid, 0.0)),
        netuid=int(v.config.netuid),
    )

    # Canonical narrative from winner
    canonical_narrative: Optional[int] = None
    if winner_hotkey and winner_hotkey in narrative_by_hotkey:
        canonical_narrative = narrative_by_hotkey[winner_hotkey]
        bt.logging.info(f"Canonical narrative progression from winner: {canonical_narrative}")

    _owner_reward = v._owner_reward_from_metagraph()
    scoring_outcome = {
        "winnerHotkey": winner_hotkey,
        "winnerUid": int(winner_uid),
        "roundId": round_id,
        "aggregatedScores": aggregated,
        "nextRoundDeadlineUnix": int(time.time()) + _cadence.SUBMISSION_WINDOW_SEC,
        "scoredSubmissionCount": scored_count,
        **( {"ownerReward": _owner_reward} if _owner_reward is not None else {} ),
    }

    # Majority / validator completion
    majority_data = fetch_majority_status(v.platform_api_url, round_id, winner_hotkey)
    if majority_data is not None:
        validator_completion = {
            "totalValidators": int(majority_data.get("totalValidators", 0)),
            "votedFinal": int(majority_data.get("votedFinal", 0)),
            "majorityReached": bool(majority_data.get("majorityReached", False)),
            "criticQuorumConfirmed": True,
        }
        log_evidence(
            "validator", "validator_majority_status",
            round_id=round_id, phase="evaluation",
            action="fetch_majority_status", reason="platform_api",
            validator_hotkey=v._validator_hotkey(),
            **validator_completion,
        )
        bt.logging.info(
            f"Majority status: {validator_completion['votedFinal']}/{validator_completion['totalValidators']} "
            f"voted final — majorityReached={validator_completion['majorityReached']}"
        )
    else:
        bt.logging.warning("Could not fetch majority status — platform will fall back to own check")
        validator_completion = {
            "totalValidators": 0, "votedFinal": 0,
            "majorityReached": False, "criticQuorumConfirmed": True,
        }

    # ── 7. Consensus gate (evaluation → finalised) ───────────────────────────
    finalise_payload = {
        "scoringOutcome": scoring_outcome,
        "canonicalNarrativeProgression": canonical_narrative,
        "validatorCompletion": validator_completion,
    }
    ok, cons_reason = await v._push_transition_via_consensus(
        "evaluation", "finalised", round_id, finalise_payload
    )
    if not ok:
        v._log_consensus_failure("evaluation", "finalised", round_id, cons_reason)
        v._liveness_note("consensus_retry_next_loop", round_id=round_id, phase="evaluation")
        v._platform_round_cache = None
        return
    v._log_consensus_success("evaluation", "finalised", round_id)
    log_vv_info(
        "finalisation_started",
        round_id=round_id, phase="evaluation->finalised",
        validator_hotkey=v._validator_hotkey(),
        reason="consensus_confirmed applying_on_chain_weights",
    )
    bt.logging.info("Evaluation->finalised consensus confirmed — applying on-chain weights")

    # ── 8. set_weights on chain ──────────────────────────────────────────────
    hk_list = list(v.metagraph.hotkeys)
    wsum = summarize_weights_for_evidence(weights, hotkeys=hk_list, top_n=16)
    log_evidence(
        "validator", "weight_computation",
        round_id=round_id, phase="evaluation",
        action="compute_weights", reason="post_winner_emission_split",
        validator_hotkey=v._validator_hotkey(),
        raw_scores_for_decision={str(k): float(v_) for k, v_ in sorted(scores.items())},
        selected_winner_uid=int(winner_uid), selected_winner_hotkey=str(winner_hotkey),
        winner_hotkey=str(winner_hotkey), winner_uid=int(winner_uid),
        **wsum,
    )
    log_vv_proof(
        "validator_emission_weights_computed",
        round_id=round_id, validator_hotkey=v._validator_hotkey(),
        nonzero_count=wsum.get("nonzero_count"),
        weight_sum=wsum.get("weight_sum"),
        weight_sum_near_one=wsum.get("weight_sum_near_one"),
        netuid=int(v.config.netuid),
    )

    if not v._validator_has_permit_for_weights(round_id=round_id):
        log_vv_error(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason="validator_permit_required_for_set_weights",
        )
        v._liveness_note("platform_set_weights_no_validator_permit", round_id=round_id, phase="evaluation")
        v._platform_round_cache = None
        return

    success, _sw_msg = v.subtensor.set_weights(
        wallet=v.wallet, netuid=v.config.netuid, uids=uids, weights=weights,
    )
    log_evidence(
        "validator", "set_weights",
        round_id=round_id, phase="evaluation",
        action="subtensor.set_weights", reason="on_chain_emission",
        validator_hotkey=v._validator_hotkey(),
        winner_hotkey=str(winner_hotkey), winner_uid=int(winner_uid),
        set_weights_success=bool(success), success=bool(success),
        netuid=int(v.config.netuid), uid_tensor_length=int(uids.numel()),
        mechid=0, uid_to_weight_nonzero=wsum.get("uid_to_weight_nonzero"),
        weight_sum=wsum.get("weight_sum"), weight_sum_near_one=wsum.get("weight_sum_near_one"),
        top_weights_preview=wsum.get("top_weights"),
    )
    maybe_dump_last_round_summary(
        round_id=round_id, mode="platform",
        winner_uid=int(winner_uid), winner_hotkey=str(winner_hotkey),
        scores=scores, uid_to_weight_nonzero=wsum.get("uid_to_weight_nonzero") or {},
        weight_sum=float(wsum.get("weight_sum") or 0.0),
        weight_sum_near_one=wsum.get("weight_sum_near_one"),
        top_weights_preview=wsum.get("top_weights"),
        set_weights_success=bool(success), netuid=int(v.config.netuid),
        validator_hotkey=v._validator_hotkey(),
    )
    log_vv_proof(
        "validator_set_weights_on_chain_result",
        round_id=round_id, validator_hotkey=v._validator_hotkey(),
        set_weights_success=bool(success), mechid=0, netuid=int(v.config.netuid),
    )

    if success:
        scoring_outcome["weightsSetAtBlock"] = int(v.subtensor.get_current_block())
        bt.logging.info(
            f"Platform weights set — winner UID {winner_uid}, "
            f"hotkey {v.metagraph.hotkeys[winner_uid]}, "
            f"block {scoring_outcome['weightsSetAtBlock']}"
        )
    else:
        bt.logging.error(
            f"Failed to set weights for round {round_id} — will retry next loop. "
            f"set_weights message: {_sw_msg}"
        )
        log_vv_error(
            "finalisation_skipped",
            round_id=round_id, phase="evaluation",
            validator_hotkey=v._validator_hotkey(),
            reason="set_weights_failed retry_next_loop marker_not_persisted",
        )
        v._liveness_note("platform_set_weights_failed", round_id=round_id, phase="evaluation")
        v._platform_round_cache = None
        return

    # ── 9. Push finalised lifecycle ──────────────────────────────────────────
    lifecycle_pushed = v._push_lifecycle(
        "finalised", round_id,
        selected_prompt_id=round_mgr.get("selected_prompt_id"),
        selected_prompt_computed=False,
        transition_source="validator_produced",
        scoring_outcome=scoring_outcome,
        canonical_narrative_progression=canonical_narrative,
        validator_completion=validator_completion,
        target_epoch_block=v._tempo_wait_target_block,
    )

    if lifecycle_pushed.accepted and lifecycle_pushed.already_finalised:
        bt.logging.info(
            "[vv_op] finalisation_already_applied round_id=%s — "
            "platform confirms round is finalised (idempotent). Writing local marker.", round_id,
        )
        log_vv_info(
            "finalisation_already_applied",
            round_id=round_id, phase="finalised",
            validator_hotkey=v._validator_hotkey(),
            reason="platform_confirmed_already_finalised writing_local_marker",
        )
        mark_round_finalised(round_id, v.db_path)
        v._platform_round_cache = None
        return

    if lifecycle_pushed.accepted and lifecycle_pushed.quorum_pending:
        bt.logging.info(
            "[vv_op] finalisation_vote_recorded round_id=%s vote_count=%d/%d — "
            "waiting for %d more validator(s). NOT writing local marker.",
            round_id, lifecycle_pushed.vote_count, lifecycle_pushed.quorum,
            max(0, lifecycle_pushed.quorum - lifecycle_pushed.vote_count),
        )
        log_vv_info(
            "finalisation_quorum_pending",
            round_id=round_id, phase="finalised",
            validator_hotkey=v._validator_hotkey(),
            reason=f"vote_recorded {lifecycle_pushed.vote_count}/{lifecycle_pushed.quorum} quorum_not_yet_met retry_next_loop",
        )
        v._platform_round_cache = None
        return

    if lifecycle_pushed.accepted:
        bt.logging.info(
            "[phase_progression] evaluation -> finalised (lifecycle accepted, quorum met) "
            "round_id=%s platform_phase=%s winner_uid=%s lifecycle_version=%s",
            lifecycle_pushed.platform_round_id_after_commit or lifecycle_pushed.round_id,
            lifecycle_pushed.platform_phase_after_commit or lifecycle_pushed.phase,
            winner_uid, lifecycle_pushed.lifecycle_version,
        )

    if not lifecycle_pushed.accepted:
        bt.logging.error(
            "Round %s lifecycle push rejected — NOT writing local marker (retry next loop). "
            "accepted=%s retryable=%s lifecycle_version=%s reason=%s "
            "platform_phase_after=%s platform_round_after=%s",
            round_id, lifecycle_pushed.accepted, lifecycle_pushed.retryable,
            lifecycle_pushed.lifecycle_version, (lifecycle_pushed.reason or "")[:500],
            lifecycle_pushed.platform_phase_after_commit, lifecycle_pushed.platform_round_id_after_commit,
        )
        log_vv_error(
            "finalisation_marker_skipped",
            round_id=round_id, phase="finalised",
            validator_hotkey=v._validator_hotkey(),
            reason="lifecycle_push_failed set_weights_may_have_committed retry_next_loop",
        )
        v._platform_round_cache = None
        return

    # ── 10. Write marker + emit evidence ────────────────────────────────────
    mark_round_finalised(round_id, v.db_path)
    log_vv_info(
        "finalisation_marker_written",
        round_id=round_id, phase="finalised",
        validator_hotkey=v._validator_hotkey(),
        reason="lifecycle_accepted_platform_path weights_set",
    )
    log_vv_info(
        "finalisation_complete",
        round_id=round_id, phase="finalised",
        validator_hotkey=v._validator_hotkey(),
        reason="weights_set marker_persisted",
    )
    log_vv_proof(
        "validator_finalisation_marker_written",
        round_id=round_id, validator_hotkey=v._validator_hotkey(),
        mode="platform", netuid=int(v.config.netuid),
    )
    bt.logging.info(f"Round {round_id} finalisation complete — marker persisted")

    _score_vals = list(scores.values())
    _qualifying = [s for s in _score_vals if s >= QUALITY_THRESHOLD]
    log_vv_judge(
        "all", "validator_round_finalised",
        round_id=round_id, validator_hotkey=v._validator_hotkey(),
        netuid=int(v.config.netuid),
        c1_functional_implementation={
            "set_weights_on_chain": True, "lifecycle_phase": "finalised",
            "total_submissions_scored": len(scores), "score_mode": score_mode,
            "idempotency_marker_written": True,
        },
        c2_incentive_mechanism={
            "winner_uid": int(winner_uid), "winner_hotkey": str(winner_hotkey),
            "winner_score": round(float(scores.get(winner_uid, 0.0)), 2),
            "quality_threshold": float(QUALITY_THRESHOLD),
            "qualifying_miners": len(_qualifying),
            "non_qualifying_miners": len(_score_vals) - len(_qualifying),
            "weight_sum_near_one": wsum.get("weight_sum_near_one"),
            "top_weights": wsum.get("top_weights"),
        },
        c3_proof_of_intelligence={
            "score_min": round(min(_score_vals), 2) if _score_vals else None,
            "score_max": round(max(_score_vals), 2) if _score_vals else None,
            "score_mean": round(sum(_score_vals) / len(_score_vals), 2) if _score_vals else None,
            "per_validator_scoring": score_mode == "per_validator",
            "all_scores_by_hotkey": {str(k): round(float(v_), 2) for k, v_ in sorted(scores.items())},
        },
        c4_scoring_robustness={
            "critic_quorum_confirmed": True,
            "all_submissions_scored_before_weights": True,
            "tempo_step_boundary_enforced": True,
            "score_mode": score_mode,
            "winner_determined_from_raw_scores_only": True,
        },
        c5_architecture={
            "mode": "platform",
            "lifecycle": "prompt_voting→submission→evaluation→finalised",
            "canonical_chain_appended": True,
        },
    )

    v._liveness_note("platform_finalisation_complete", round_id=round_id, phase="finalised")
    v._emit_validator_event(
        EVENT_FINALISATION, round_id,
        {
            "mode": "platform", "variant": "weights_set",
            "winner_uid": int(winner_uid), "winner_hotkey": str(winner_hotkey),
            "winner_score": round(float(scores.get(winner_uid, 0.0)), 2),
            "weights_set_at_block": scoring_outcome.get("weightsSetAtBlock"),
            "scored_submission_count": len(scores),
            "qualifying_count": len(_qualifying),
            "non_qualifying_count": len(_score_vals) - len(_qualifying),
            "quality_threshold": float(QUALITY_THRESHOLD),
            "weight_sum_near_one": wsum.get("weight_sum_near_one"),
            "top_weights": (wsum.get("top_weights") or [])[:8],
            "owner_reward_tao": _owner_reward,
        },
    )

    from vividverse.validator.critic_transfers import execute_critic_transfers
    execute_critic_transfers(
        subtensor=v.subtensor, wallet=v.wallet,
        platform_api_url=v.platform_api_url, round_id=round_id,
    )
    v._platform_round_cache = None
