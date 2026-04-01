#!/usr/bin/env python3
"""
neurons/validator.py

Vividverse Validator — Entry Point

Usage:
    python neurons/validator.py --netuid 1 --subtensor.network local \
        --wallet.name validator --wallet.hotkey default

The validator manages the subnet lifecycle:
  1. Broadcasts round state to miners
  2. Collects submission metadata
  3. Waits for critic scores (AI Vision in production)
  4. Computes and sets weights on chain
  5. Updates canonical chain with winner

Logging (all via bittensor `bt.logging`; no extra log frameworks):
  - [vv_evidence] … JSON lines — full structured audit (see vividverse.utils.evidence_log).
  - [vv_op] … — operational milestones (vividverse.utils.ops_log).
  - [vv_op] action=cadence_routing — each platform step: phase, deadline unix, gate flags
    (submission_open, submission_just_closed, evaluation_window, finalisation_due). Grep `cadence_routing`.
  - [vv_op] action=cadence_finalisation — entering `_finalise_round_platform` after evaluation deadline.
  - [vv_proof] … — short milestone lines proving what step completed (same round_id / trace_key
    as evidence); grep `proof=` for demos / Round 2 packs.

Example (abbreviated):
  [vv_proof] proof=validator_scoring_inputs_loaded round_id=4 trace_key=r4|v…|m-|uid- source=platform_api …
  [vv_proof] proof=validator_winner_fixed_from_raw_scores round_id=4 winner_uid=2 …
  [vv_proof] proof=validator_set_weights_on_chain_result round_id=4 set_weights_success=true …
  [vv_proof] proof=validator_finalisation_marker_written round_id=4 mode=platform …

Optional env (main loop / Platform health):
  VALIDATOR_STEP_INTERVAL_SEC — seconds between steps (default 10).
  VALIDATOR_STEP_INTERVAL_DEGRADED_SEC — when Platform snapshot fails, sleep at most this many
    seconds before next step (default 5); set 0 to disable faster retry.
  VALIDATOR_PLATFORM_FAILURE_ALERT_THRESHOLD — consecutive snapshot failures before a debounced
    [vv_op] platform_api_degraded warning (default 5); 0 disables.
  VALIDATOR_PLATFORM_ALERT_COOLDOWN_SEC — min seconds between degraded alerts (default 120).

Finalisation markers (SQLite finalisation_markers in vividverse.db):
  - Idempotent guard for set_weights / lifecycle / canonical updates per round_id.
  - Stale markers (local row present while platform Round is still submission/evaluation) are
    removed automatically — see vividverse.utils.finalisation_marker and grep
    finalisation_marker_removed_due_to_mismatch / finalisation_marker_written / finalisation_marker_skipped.

Platform vs UI alignment (read-only, no extra weight/reward effects):
  - Each step: GET /api/subnet/state is merged with GET /api/rounds/current. When filmCycleState is
    round_active, activeRound.roundId is authoritative; when filmCycleState is prompt_voting, any
    stale roundId from rounds/current is cleared so routing matches the UI.
  - Operator banner: startup prefetch runs before the first printed phase= line (not after).
  - Phase display uses platform-derived values when PLATFORM_API_URL is set (_phase_display_for_operator).
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time
import traceback
from typing import Optional, Dict, Any, Union, Tuple

import bittensor as bt
import torch

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

# Ensure vividverse package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vividverse.utils.finalisation_marker import (
    init_db as init_finalisation_markers_db,
    is_round_finalised,
    mark_round_finalised,
    remove_stale_marker_if_platform_phase_active,
)
from vividverse.utils.platform_round import get_round_id, deadline_only_round_bootstrap
from vividverse.validator.finalise import finalise_round_platform
from vividverse.validator.reward import compute_weights, identify_winner
from vividverse.validator.critic_transfers import execute_critic_transfers
from vividverse.validator.lifecycle_push_result import LifecyclePushResult
from vividverse.validator.platform_fetch import (
    fetch_current_round,
    fetch_round_state,
    fetch_subnet_state_summary,
    fetch_platform_scores_extended,
    fetch_majority_status,
    fetch_critic_quorum_status,
    fetch_prompt_votes,
    push_lifecycle_to_platform,
    fetch_validator_sync_status,
)
from vividverse.validator.consensus_fetch import (
    get_last_checkpoint,
    sync_from_checkpoint_and_propose_or_vote,
)
from vividverse.validator.reward import QUALITY_THRESHOLD
from vividverse.contracts.prompt_voting_completion import (
    compute_prompt_voting_decision,
    PromptVotingDecision,
    DEFAULT_QUORUM_RATIO,
    NoSelectionReason,
)
from vividverse.contracts.round_registry import compute_round_deadlines
# Cadence: subnet-owner controlled in vividverse.contracts.cadence.
# Imported as module reference so cadence.reload() in step() picks up live edits
# to subnet_settings.json without restarting the validator.
import vividverse.contracts.cadence as _cadence
from vividverse.contracts.tempo import is_tempo_complete, compute_next_epoch_block
from vividverse.utils.ops_log import log_vv_debug, log_vv_error, log_vv_info, log_vv_success, log_vv_warning
from vividverse.utils.evidence_log import log_evidence, log_vv_proof, log_vv_judge, log_round_in_progress_snapshot, summarize_weights_for_evidence
from vividverse.utils.last_round_summary import maybe_dump_last_round_summary
from vividverse.utils.liveness import (
    build_tracker_from_env,
    register_liveness_tracker,
)
from vividverse.validator.events import (
    EVENT_EVALUATION_STARTED,
    EVENT_FINALISATION,
    EVENT_PROMPT_VOTING_COMPLETE,
    EVENT_RESTART,
    EVENT_ROUND_BOOTSTRAP,
    EVENT_SUBMISSION_WINDOW_CLOSED,
    emit_validator_event,
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Main async loop: time between `step()` calls (lower when Platform snapshot is failing).
VALIDATOR_STEP_INTERVAL_SEC = max(0.5, _env_float("VALIDATOR_STEP_INTERVAL_SEC", 10.0))
_dgr = _env_float("VALIDATOR_STEP_INTERVAL_DEGRADED_SEC", 5.0)
VALIDATOR_STEP_INTERVAL_DEGRADED_SEC = 0.0 if _dgr <= 0 else max(0.5, _dgr)
# When waiting for tempo step boundary (all other criteria met), poll this frequently.
# Bittensor block time is ~12s; polling at 3s ensures we don't miss the single-block window.
VALIDATOR_STEP_INTERVAL_TEMPO_WAIT_SEC = max(
    0.5, _env_float("VALIDATOR_STEP_INTERVAL_TEMPO_WAIT_SEC", 3.0)
)

VALIDATOR_PLATFORM_FAILURE_ALERT_THRESHOLD = max(
    0, _env_int("VALIDATOR_PLATFORM_FAILURE_ALERT_THRESHOLD", 5)
)
VALIDATOR_PLATFORM_ALERT_COOLDOWN_SEC = max(
    0.0, _env_float("VALIDATOR_PLATFORM_ALERT_COOLDOWN_SEC", 120.0)
)


def _classify_validator_exception(exc: Exception) -> str:
    """Short, grep-friendly reason for [vv_op] validator_step_failed."""
    name = type(exc).__name__
    if requests is not None and isinstance(
        exc, requests.exceptions.RequestException
    ):
        return f"http_request:{name}:{exc}"
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return f"os_network:{name}:{exc}"
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return f"parse:{name}:{exc}"
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)):
        return f"data:{name}:{exc}"
    return f"other:{name}:{exc}"


def get_miner_uids(metagraph: "bt.Metagraph") -> "list[int]":
    """UIDs of all non-validator neurons — for evidence / diagnostics."""
    return [
        uid for uid in range(metagraph.n.item())
        if not metagraph.validator_permit[uid].item()
    ]


class Validator:
    """
    Vividverse validator node.

    Manages round lifecycle and incentive distribution.
    Monitors platform for phase transitions; sets weights on chain via subtensor.

    Consensus (CONSENSUS_ENABLED): see project/docs/CONSENSUS_VALIDATOR_RULES.md.
    Gated transitions use Platform quorum; no unilateral lifecycle push on failure.
    Consensus HTTP uses the same retry/backoff as other Platform calls (PLATFORM_HTTP_RETRY_*);
    per-attempt timeout: CONSENSUS_API_TIMEOUT (default 20s).
    Non-gated pushes (deadline, phase echo, no-selection outcome) stay validator-local.

    Platform reads: see project/docs/VALIDATOR_PLATFORM_READS.md. Irreversible paths use
    fresh HTTP fetches per step (or immediately before finalisation); TTL cache is not authority.

    Main loop timing and Platform health: see module docstring (VALIDATOR_STEP_INTERVAL_*,
    VALIDATOR_PLATFORM_* alert env). Step failures log [vv_op] validator_step_failed with a
    classified reason, then re-raise (KeyboardInterrupt / SystemExit are not caught here).
    """

    def __init__(self, config) -> None:
        self.config = config

        # Bittensor objects
        self.wallet = bt.Wallet(config=config)
        self.subtensor = bt.Subtensor(config=config)
        self.metagraph = self.subtensor.metagraph(config.netuid)

        # State management — only finalisation markers need local storage;
        # submissions and round state are owned by the platform.
        self.db_path = getattr(config, "db_path", "vividverse.db")
        init_finalisation_markers_db(self.db_path)

        self._last_liveness_action = "init"
        self._liveness_round_id: Optional[Union[int, str]] = None
        self._liveness_phase = "-"
        self._liveness = build_tracker_from_env(self._validator_hotkey())
        register_liveness_tracker(self._liveness)

        platform_url = getattr(config, "platform_api_url", None)
        self.platform_api_url: str = platform_url or os.environ.get("VALIDATOR_PLATFORM_API_URL", "")
        # Last successful fetch_round_state snapshot (write-through after fresh reads only).
        # NOT an authority for irreversible actions — never branch on cache without a fresh read first.
        # Safe uses: optional diagnostics, future non-critical consumers (see _get_cached_round_state_optional).
        self._platform_round_cache: Optional[Dict[str, Any]] = None
        self._platform_round_cache_ts: float = 0.0
        self._platform_cache_ttl: float = 30.0  # seconds — only for optional cached reads
        # Platform snapshot streak (fetch_current_round / fetch_round_state); drives degraded poll + alerts.
        self._platform_consecutive_failures: int = 0
        self._last_platform_fail_alert_mono: float = 0.0
        self._printed_first_step_ok: bool = False
        # Set True when tempo is the only remaining gate; drives faster polling in the main loop.
        self._in_tempo_wait: bool = False
        # Epoch-deadline fallback: block number of the next step boundary, computed when all other
        # criteria are first met. If blocks_since_last_step==0 is never observed, finalisation
        # proceeds anyway once current_block >= this value (guaranteed within one tempo epoch).
        self._tempo_wait_target_block: Optional[int] = None
        self._tempo_wait_round_id: Optional[int] = None
        # Track which (round_id, phase) combinations have been announced at SUCCESS level so we
        # only fire the "phase_entered" milestone once per phase entry even across repeated steps.
        self._last_phase_announced: Optional[tuple] = None
        # Step counter per round for throttling in-progress snapshots (emit every ~6 steps).
        self._eval_snapshot_step: Dict[int, int] = {}

        # Fail-fast: require platform URL before any network calls.
        if not self.platform_api_url:
            raise RuntimeError(
                "PLATFORM_API_URL is not configured. "
                "Set the VALIDATOR_PLATFORM_API_URL environment variable (or --platform-api-url) "
                "before starting the validator."
            )

        # Consensus-gated phase transitions: always active.
        # Multiple validators must agree before phases advance; quorum is dynamic
        # based on active validator count (majority of those seen in last 2 hours).
        # With 1 active validator, quorum = 1 and the platform validator proceeds alone.
        cp = get_last_checkpoint(self.platform_api_url)
        if cp and cp.get("checkpoint"):
            c = cp["checkpoint"]
            bt.logging.info(
                f"Consensus: resuming from last checkpoint phase={c.get('phase')} "
                f"roundId={c.get('roundId')}"
            )

        # Startup sync: fetch platform state and log where we are in the lifecycle.
        # This gives the operator immediate visibility on join — no need to dig through logs.
        self._startup_sync_logged = False
        # Pending gate: True when this validator is registered but not yet quorum-eligible.
        # Re-checked every 30 steps so the validator notices when it gets promoted.
        self._is_pending_validator: bool = False
        # Time-based pending re-poll (every 3 minutes) — more reliable than step counting
        # since step duration can vary. Catching re-pending within 3 min is sufficient
        # because the heartbeat window is 2 hours and normal steps push every ~10s.
        self._last_pending_check_mono: float = 0.0
        # Time-based prompt_voting heartbeat push while waiting for votes.
        # Ensures the validator registers its heartbeat even before vote thresholds
        # are met — otherwise the heartbeat is never created until a phase transition.
        self._last_pv_heartbeat_mono: float = 0.0

        bt.logging.info(f"Validator initialized with hotkey: {self.wallet.hotkey.ss58_address}")
        _chain_ep = getattr(self.config.subtensor, "chain_endpoint", None) or os.environ.get(
            "VALIDATOR_SUBTENSOR_CHAIN_ENDPOINT", "-"
        )
        _nw = getattr(self.config.subtensor, "network", None) or "-"
        try:
            _muid = get_miner_uids(self.metagraph)
            log_evidence(
                "validator",
                "startup",
                action="validator_init",
                reason="metagraph_synced",
                validator_hotkey=self._validator_hotkey(),
                netuid=int(self.config.netuid),
                chain_endpoint=str(_chain_ep),
                subtensor_network=str(_nw),
                miner_uid_count=len(_muid),
                total_neurons=int(self.metagraph.n.item()),
                platform_api_configured=bool(self.platform_api_url),
                consensus_mode="dynamic_quorum",
            )
        except Exception as ex:
            bt.logging.warning(
                f"[vv_evidence] startup snapshot failed: {type(ex).__name__}: {ex}"
            )
        bt.logging.info("Platform mode: using Platform API for round state and scores")
        if not os.environ.get("VALIDATOR_INGEST_SECRET"):
            bt.logging.info(
                "[vv_config] VALIDATOR_INGEST_SECRET not set — "
                "Platform will verify this validator's hotkey against the metagraph. "
                "Set VALIDATOR_INGEST_SECRET (matching the Platform) to use the faster secret-based path."
            )

    def _log_startup_sync(self) -> None:
        """
        Fetch and log the platform's current lifecycle state on first step.
        Gives the operator immediate visibility on what phase/round the network
        is in, how many other validators are active, and what this validator
        should do next.
        """
        hotkey = self._validator_hotkey()
        bt.logging.info(
            f"[startup_sync] Fetching platform lifecycle sync status "
            f"hotkey={hotkey[:8]}... platform={self.platform_api_url}"
        )

        sync = fetch_validator_sync_status(self.platform_api_url, validator_hotkey=hotkey)

        if sync is None:
            bt.logging.warning(
                "[startup_sync] Could not reach platform sync-status endpoint. "
                "Setting _is_pending_validator=True (conservative) — will not participate "
                "in consensus until status confirmed. Will retry in 3 minutes via pending re-poll. "
                "Ensure PLATFORM_API_URL is reachable."
            )
            self._is_pending_validator = True
            return

        cs = sync.get("currentState", {})
        av = sync.get("activeValidators", {})
        jv = sync.get("joiningValidator", {})
        summary = sync.get("summary", "")

        phase = cs.get("phase", "unknown")
        round_id = cs.get("roundId")
        frozen = cs.get("frozen", False)
        validator_online = cs.get("validatorOnline", False)
        active_count = av.get("count", 0)
        pending_count = av.get("pendingCount", 0)
        quorum_required = av.get("quorumRequired", 1)

        # ── Core state line ──────────────────────────────────────────────────
        bt.logging.info(
            f"\n"
            f"╔══════════════════════════════════════════════════════════╗\n"
            f"║              VALIDATOR LIFECYCLE SYNC STATUS             ║\n"
            f"╠══════════════════════════════════════════════════════════╣\n"
            f"║  Phase:            {phase:<38}║\n"
            f"║  Round ID:         {str(round_id or 'none'):<38}║\n"
            f"║  Frozen:           {str(frozen):<38}║\n"
            f"║  Lifecycle online: {str(validator_online):<38}║\n"
            f"║  Active validators:{active_count:<38}║\n"
            f"║  Pending validators:{pending_count:<37}║\n"
            f"║  Quorum required:  {quorum_required:<38}║\n"
            f"║  Consensus:        {'dynamic_quorum (always active)':<38}║\n"
            f"╚══════════════════════════════════════════════════════════╝"
        )

        # ── Active validator list ────────────────────────────────────────────
        validators = av.get("validators", [])
        if validators:
            bt.logging.info(
                "[startup_sync] Validators in heartbeat window "
                "(active=counts toward quorum, pending=waiting for prompt_voting gate):"
            )
            for v in validators:
                marker = " ← YOU" if v.get("hotkey") == hotkey else ""
                status = v.get("status", "?")
                bt.logging.info(
                    f"  [{status}] {v.get('hotkeyShort', v.get('hotkey', '?')[:12])} "
                    f"phase={v.get('phase', '?')} "
                    f"round={v.get('roundId', '?')} "
                    f"last_seen={v.get('lastSeenSecondsAgo', '?')}s ago"
                    f"{marker}"
                )
        else:
            bt.logging.info(
                "[startup_sync] No validators in heartbeat window. "
                "You are the first validator connecting — quorum = 1 until others join."
            )

        # ── Pending proposals ─────────────────────────────────────────────────
        proposals = sync.get("pendingProposals", [])
        if proposals:
            bt.logging.info(f"[startup_sync] {len(proposals)} pending phase transition proposal(s):")
            for p in proposals:
                bt.logging.info(
                    f"  [{p.get('fromPhase')}→{p.get('toPhase')}] "
                    f"round={p.get('roundId')} votes={p.get('voteCount')}/{quorum_required} "
                    f"age={p.get('ageMs', 0) // 1000}s"
                )

        # ── Alignment + instruction ───────────────────────────────────────────
        if jv:
            alignment = jv.get("phaseAlignment", "unknown")
            hb_status = jv.get("status", "unknown")
            recognized = jv.get("recognizedAsActive", False)  # True only if status="active"
            is_pending = jv.get("isPending", False)
            wait_for_phase = jv.get("waitForPhase")
            instruction = jv.get("instruction", "")
            # Cache pending state so the step loop can log reminders.
            self._is_pending_validator = is_pending

            if alignment == "aligned" and recognized:
                bt.logging.info(f"[startup_sync] ✓ Aligned with network ({phase}, round {round_id})")
            elif is_pending:
                bt.logging.info(
                    f"[startup_sync] ⏳ PENDING QUORUM ADMISSION — joined mid-round. "
                    f"status=pending, current phase={phase}, round={round_id}. "
                    f"Will be promoted to active quorum at next {wait_for_phase or 'prompt_voting'} "
                    f"phase (requires ≥1 active critic in your pool). "
                    f"Continuing to push lifecycle heartbeats to stay visible."
                )
            elif not recognized:
                bt.logging.info(
                    f"[startup_sync] ⚡ NEW VALIDATOR — not yet registered. "
                    f"Push a lifecycle state to register your heartbeat."
                )
            else:
                bt.logging.warning(
                    f"[startup_sync] ⚠ OUT OF SYNC — local state differs from network. "
                    f"alignment={alignment}"
                )

            if instruction:
                bt.logging.info(f"[startup_sync] NEXT ACTION: {instruction}")

        if frozen:
            bt.logging.error(
                "[startup_sync] ⚠ ROUND IS FROZEN — the lifecycle validator went offline. "
                "If you are the platform validator, push a lifecycle heartbeat immediately."
            )

        # Cross-check local finalisation marker against platform phase.
        # If we have a local record of finalising this round but platform shows an
        # active (non-finalised) phase, the platform DB may have been restored from backup.
        # The stale-marker removal loop will clean this up on the next active step,
        # but warn early so operators can investigate rather than silently skipping finalisation.
        if round_id is not None and phase not in ("finalised",):
            try:
                local_finalised = is_round_finalised(round_id, self.db_path)
                if local_finalised:
                    bt.logging.warning(
                        f"[startup_sync] ⚠ FINALISATION MARKER MISMATCH — "
                        f"local SQLite records round {round_id} as finalised, "
                        f"but platform shows phase={phase}. "
                        f"Possible cause: platform DB was restored from a backup taken before finalisation. "
                        f"The stale marker will be removed automatically on the next active step. "
                        f"Monitor whether the platform re-reaches 'finalised' — if not, "
                        f"a manual re-push of the finalised lifecycle may be required."
                    )
            except Exception as _fme:
                bt.logging.debug(f"[startup_sync] Could not check local finalisation marker: {_fme}")

        # ── Structured evidence log ───────────────────────────────────────────
        log_evidence(
            "validator",
            "startup_sync",
            action="lifecycle_sync_check",
            reason="validator_startup",
            validator_hotkey=hotkey,
            platform_phase=phase,
            platform_round_id=round_id,
            frozen=frozen,
            active_validators=active_count,
            quorum_required=quorum_required,
            alignment=jv.get("phaseAlignment", "unknown") if jv else "not_checked",
            recognized=jv.get("recognizedAsActive", False) if jv else False,
            heartbeat_status=jv.get("status", "unknown") if jv else "unknown",
            is_pending=jv.get("isPending", False) if jv else False,
            wait_for_phase=jv.get("waitForPhase") if jv else None,
            summary=summary,
        )

    def _validator_hotkey(self) -> str:
        """SS58 for structured [vv_op] lines; '-' if unavailable."""
        if self.wallet and getattr(self.wallet, "hotkey", None):
            return self.wallet.hotkey.ss58_address
        return "-"

    def _owner_reward_from_metagraph(self) -> Optional[float]:
        """
        Read the subnet owner's TAO dividend from the current metagraph snapshot.

        The validator IS the subnet owner (same hotkey). After set_weights the
        chain distributes dividends; tao_dividends_per_hotkey holds the per-epoch
        TAO amount for each hotkey. Returns None when the field is unavailable or
        the value cannot be parsed — callers omit ownerReward from the lifecycle
        push in that case rather than sending 0.
        """
        owner_hk = self._validator_hotkey()
        if not owner_hk or owner_hk == "-":
            return None
        try:
            tao_divs = getattr(self.metagraph, "tao_dividends_per_hotkey", None)
            if isinstance(tao_divs, dict):
                v = tao_divs.get(owner_hk)
                if v is not None:
                    return float(v)
        except Exception as _e:
            bt.logging.warning(f"_owner_reward_from_metagraph: could not read tao_dividends_per_hotkey: {_e}")
        return None

    def _validator_has_permit_for_weights(self, round_id: Optional[int] = None) -> bool:
        """
        Bittensor requires validator_permit=True for set_weights to succeed (BT-01).
        Short-circuit with a clear log if this wallet is not permitted.
        """
        try:
            hk = self.wallet.hotkey.ss58_address
            hotkeys = list(self.metagraph.hotkeys)
            if hk not in hotkeys:
                bt.logging.error(
                    "Validator hotkey is not registered on this subnet metagraph — "
                    "skipping set_weights; register with btcli subnet register."
                )
                return False
            uid = hotkeys.index(hk)
            if not self.metagraph.validator_permit[uid].item():
                bt.logging.error(
                    f"validator_permit is False for UID {uid} — set_weights will fail on chain. "
                    "Stake per subnet requirements and wait for tempo."
                )
                log_evidence(
                    "validator",
                    "set_weights_skipped",
                    round_id=round_id,
                    phase="evaluation",
                    action="validator_permit_check",
                    reason="validator_permit_false",
                    validator_hotkey=hk,
                    validator_uid=int(uid),
                )
                return False
            return True
        except Exception as e:
            bt.logging.error(f"validator_permit check failed: {e}")
            return False

    def _phase_display_for_operator(self) -> str:
        """
        Operator-facing phase string.

        Prefer last successful step liveness, then cached round_phase from fetch_round_state.
        """
        if self._liveness_phase != "-":
            return self._liveness_phase
        c = self._platform_round_cache
        if c:
            rp = c.get("round_phase")
            if rp is not None and str(rp).strip() != "":
                return str(rp)
        return "awaiting_platform_snapshot"

    def _liveness_note(
        self,
        action: str,
        round_id: Optional[Union[int, str]] = None,
        phase: Optional[str] = None,
        *,
        eval_touch: bool = False,
    ) -> None:
        """Update last action for stall diagnostics and optional eval timestamp."""
        self._last_liveness_action = action
        if round_id is not None:
            self._liveness_round_id = round_id
        if phase is not None:
            self._liveness_phase = phase
        if eval_touch:
            self._liveness.touch_eval(
                action,
                round_id=self._liveness_round_id,
                phase=self._liveness_phase,
            )

    def _record_platform_snapshot_failure(self) -> None:
        """Increment failure streak; debounced warning after repeated Platform snapshot errors."""
        self._platform_consecutive_failures += 1
        thr = VALIDATOR_PLATFORM_FAILURE_ALERT_THRESHOLD
        if thr <= 0 or self._platform_consecutive_failures < thr:
            return
        now = time.monotonic()
        if (
            VALIDATOR_PLATFORM_ALERT_COOLDOWN_SEC > 0
            and now - self._last_platform_fail_alert_mono
            < VALIDATOR_PLATFORM_ALERT_COOLDOWN_SEC
        ):
            return
        self._last_platform_fail_alert_mono = now
        log_vv_warning(
            "platform_api_degraded",
            round_id=self._liveness_round_id,
            phase=self._liveness_phase,
            validator_hotkey=self._validator_hotkey(),
            reason=(
                f"consecutive_snapshot_failures={self._platform_consecutive_failures} "
                "hint=verify_PLATFORM_API_URL_is_reachable_and_correct"
            ),
        )

    def _record_platform_snapshot_success(self) -> None:
        """Clear streak; log recovery if we had been failing."""
        prev = self._platform_consecutive_failures
        self._platform_consecutive_failures = 0
        if prev > 0:
            log_vv_info(
                "platform_api_recovered",
                round_id=self._liveness_round_id,
                phase=self._liveness_phase,
                validator_hotkey=self._validator_hotkey(),
                reason=f"after_failures={prev}",
            )

    def _emit_validator_event(
        self,
        event_type: str,
        round_id: Optional[Union[int, str]],
        payload: Dict[str, Any],
    ) -> None:
        """Additive audit event to Platform (no-op without platform_api_url)."""
        emit_validator_event(
            self.platform_api_url,
            self._validator_hotkey(),
            event_type,
            round_id,
            payload,
        )

    def _load_platform_snapshot_for_step(
        self,
    ) -> Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]], str]]:
        """
        Authoritative snapshot for this step: always fresh HTTP reads (no TTL shortcut).

        Used for routing: phase transitions, finalisation triggers, submission window edges.
        Stale cache must not drive these — if a fetch fails, return None and exit the step.

        Returns:
            (current, state, read_mode) where read_mode is always 'fresh' here.
            state is None when Platform has no active round (prompt voting / between rounds).
        """
        bt.logging.debug("[platform_read] fetch_current_round source=fresh purpose=step_routing")
        current = fetch_current_round(self.platform_api_url)
        if not current:
            bt.logging.warning(
                "[platform_read] fetch_current_round failed — exit step safely (retry next loop)"
            )
            self._record_platform_snapshot_failure()
            return None

        # Canonical alignment with GET /api/subnet/state (same payload as the UI).  Resolves
        # stale or null roundId from /rounds/current without changing weight/reward authority.
        overlay = fetch_subnet_state_summary(self.platform_api_url)
        if overlay:
            fcs = overlay.get("filmCycleState")
            if fcs == "prompt_voting":
                if current.get("roundId") is not None:
                    log_vv_warning(
                        "platform_alignment_subnet_prompt_voting",
                        round_id=current.get("roundId"),
                        phase="prompt_voting",
                        validator_hotkey=self._validator_hotkey(),
                        reason=(
                            "subnet/state filmCycleState=prompt_voting — clearing stale "
                            "roundId from rounds/current (align with UI)"
                        ),
                    )
                    current = dict(current)
                    current["roundId"] = None
                self._record_platform_snapshot_success()
                return (current, None, "fresh")
            if fcs == "round_active":
                ar = overlay.get("activeRound")
                if isinstance(ar, dict) and ar.get("roundId") is not None:
                    try:
                        rid_align = int(ar["roundId"])
                    except (TypeError, ValueError) as e:
                        bt.logging.warning(
                            "[platform_read] roundId alignment: could not parse int from "
                            f"{ar.get('roundId')!r}: {e} — skipping alignment"
                        )
                        rid_align = None
                    if rid_align is not None:
                        cur_rid = current.get("roundId")
                        if cur_rid is None or int(cur_rid) != rid_align:
                            bt.logging.info(
                                "[platform_read] subnet_state_align roundId=%s "
                                "rounds_current_was=%s (canonical activeRound from subnet/state)",
                                rid_align,
                                cur_rid,
                            )
                        current = dict(current)
                        current["roundId"] = rid_align

        rid = current.get("roundId")
        if rid is None:
            self._record_platform_snapshot_success()
            return (current, None, "fresh")

        bt.logging.debug(
            f"[platform_read] fetch_round_state source=fresh purpose=step_routing round_id={rid}"
        )
        state = fetch_round_state(self.platform_api_url, int(rid))
        if not state:
            bt.logging.warning(
                f"[platform_read] fetch_round_state failed round_id={rid} — exit step safely "
                "(retry next loop)"
            )
            self._record_platform_snapshot_failure()
            return None

        self._record_platform_snapshot_success()
        self._platform_round_cache = state
        self._platform_round_cache_ts = time.time()
        return (current, state, "fresh")

    def _bootstrap_platform_display_phase(self) -> None:
        """
        Prefetch one platform snapshot before the startup banner.

        Ensures operator-facing phase matches canonical platform cadence (same as UI)
        before the first step.
        """
        try:
            snap = self._load_platform_snapshot_for_step()
            if snap is None:
                return
            current, state, _read_mode = snap
            self._reconcile_platform_lifecycle_state(current, state)
            round_id = current.get("roundId") if current else None
            if state is not None:
                ph = str(state.get("round_phase", "submission"))
            else:
                cur_phase = current.get("phase") if current else None
                ph = str(
                    cur_phase
                    if cur_phase is not None
                    else ("prompt_voting" if not round_id else "finalised")
                )
            self._liveness_note(
                "startup_platform_prefetch",
                round_id=round_id,
                phase=ph,
                eval_touch=True,
            )
        except Exception as ex:
            bt.logging.warning(
                "[platform_read] startup_platform_prefetch failed — %s: %s",
                type(ex).__name__,
                ex,
            )

    def _get_cached_round_state_optional(self, round_id: int) -> Optional[Dict[str, Any]]:
        """
        Non-authoritative read: TTL cache hit only if same round and within TTL.

        Safe for: optional logging, best-effort display — NOT for finalisation, restarts,
        phase transitions, or prompt voting decisions. Prefer fresh helpers for those.
        """
        now = time.time()
        c = self._platform_round_cache
        c_rid = c.get("round_id") if c else None
        if (
            c is not None
            and c_rid is not None
            and int(c_rid) == int(round_id)
            and now - self._platform_round_cache_ts < self._platform_cache_ttl
        ):
            bt.logging.debug(
                f"[platform_read] round state source=cached round_id={round_id} "
                f"age_s={now - self._platform_round_cache_ts:.1f} (non-authoritative)"
            )
            return c
        return None

    def _platform_phase_checks(
        self, state: Dict[str, Any]
    ) -> tuple[bool, bool, bool, bool]:
        """Return (is_submission, submission_just_closed, is_evaluation, finalisation_due)."""
        phase = state.get("round_phase", "submission")
        now = time.time()
        sub_deadline = state.get("submission_deadline_unix", 0) or 0
        eval_deadline = state.get("evaluation_deadline_unix", 0) or 0

        is_submission = phase == "submission" and now < sub_deadline
        submission_just_closed = phase == "submission" and now >= sub_deadline
        is_evaluation = phase == "evaluation" and now < eval_deadline
        finalisation_due = phase == "evaluation" and now >= eval_deadline
        if phase == "finalised":
            finalisation_due = True  # Platform finalised; we may still set weights

        return is_submission, submission_just_closed, is_evaluation, finalisation_due

    def _reconcile_platform_lifecycle_state(
        self,
        current: Dict[str, Any],
        state: Optional[Dict[str, Any]],
    ) -> None:
        """
        Stage 4: compare platform snapshot vs local finalisation marker.

        Does not move authority to the platform; optional repairs are env-gated:
          Case 1: stale marker vs platform submission/evaluation — always DELETE the local
            marker when detected (safe recovery; cannot permanently block an active round).
          VALIDATOR_RECONCILE_MARKER_FROM_PLATFORM=1 — Case 2: WRITE marker when platform
            round_phase is finalised (only when operator trusts platform/chain alignment).
            Warning: writing the marker makes _finalise_round_platform skip on idempotency;
            use only when another validator already finalised on-chain or you accept skipping
            local set_weights for this round.

        Case 3 (push rejected, round still active): enforced by Stage 2 (no marker without
        accepted lifecycle push); we only log the healthy active-round path at debug.
        """
        if not current:
            return

        mark_from_platform = os.environ.get(
            "VALIDATOR_RECONCILE_MARKER_FROM_PLATFORM", "0"
        ) in ("1", "true", "yes")

        if current.get("roundId") is None:
            bt.logging.debug(
                "[lifecycle_reconciliation] skip no active round_id (prompt_voting path)"
            )
            return

        if state is None:
            return

        try:
            rid_int = int(current.get("roundId"))
        except (TypeError, ValueError) as e:
            bt.logging.warning(
                f"[lifecycle_reconciliation] could not parse roundId "
                f"{current.get('roundId')!r}: {e} — skipping reconciliation"
            )
            return

        sub = str(state.get("round_phase", "submission")).lower()
        marker = is_round_finalised(rid_int, self.db_path)

        bt.logging.debug(
            f"[lifecycle_reconciliation] snapshot platform_round_id={rid_int} "
            f"platform_round_phase={sub} marker_finalised={marker}"
        )

        # Case 1: local marker says we finalised this round, platform still active — remove marker
        if marker and sub in ("submission", "evaluation"):
            log_vv_error(
                "reconciliation_case_1_stale_marker_vs_platform",
                round_id=rid_int,
                phase=sub,
                validator_hotkey=self._validator_hotkey(),
                reason=(
                    f"local finalisation marker present but platform round_phase={sub} "
                    "for same round — clearing stale marker (safe recovery)"
                ),
            )
            bt.logging.error(
                f"[lifecycle_reconciliation] case=1 stale_marker platform_phase={sub} round_id={rid_int}"
            )
            if remove_stale_marker_if_platform_phase_active(rid_int, sub, self.db_path):
                bt.logging.warning(
                    json.dumps(
                        {
                            "event": "finalisation_marker_removed_due_to_mismatch",
                            "round_id": rid_int,
                            "platform_round_phase": sub,
                            "source": "lifecycle_reconciliation",
                        }
                    )
                )
                log_vv_warning(
                    "finalisation_marker_removed_due_to_mismatch",
                    round_id=rid_int,
                    phase=sub,
                    validator_hotkey=self._validator_hotkey(),
                    reason="lifecycle_reconciliation platform_active_vs_local_marker",
                )
            return

        # Case 2: platform finalised; local marker missing (catch-up only when configured)
        if sub == "finalised" and not marker:
            log_vv_info(
                "reconciliation_case_2_platform_finalised_no_local_marker",
                round_id=rid_int,
                phase="finalised",
                validator_hotkey=self._validator_hotkey(),
                reason=(
                    "platform round_phase=finalised with proof from fetch_round_state; "
                    "optional local marker via VALIDATOR_RECONCILE_MARKER_FROM_PLATFORM"
                ),
            )
            bt.logging.info(
                f"[lifecycle_reconciliation] case=2 platform_finalised_local_no_marker "
                f"round_id={rid_int} mark_from_platform_env={mark_from_platform}"
            )
            if mark_from_platform:
                mark_round_finalised(rid_int, self.db_path)
                log_vv_info(
                    "finalisation_marker_written",
                    round_id=rid_int,
                    phase="finalised",
                    validator_hotkey=self._validator_hotkey(),
                    reason="VALIDATOR_RECONCILE_MARKER_FROM_PLATFORM aligned_local_marker",
                )
            return

        # Case 3: active round, no marker — normal until finalisation + accepted lifecycle push
        if not marker and sub in ("submission", "evaluation"):
            bt.logging.debug(
                f"[lifecycle_reconciliation] case=3_active_round_no_marker round_id={rid_int} phase={sub}"
            )
            return

        # Case 4: aligned (marker + platform finalised, or benign combinations)
        if marker and sub == "finalised":
            bt.logging.debug(
                f"[lifecycle_reconciliation] case=4_aligned round_id={rid_int} marker=1 platform=finalised"
            )
            return

        bt.logging.debug(
            f"[lifecycle_reconciliation] case=4_noop round_id={rid_int} platform_phase={sub} marker={marker}"
        )

    def _log_platform_cadence_snapshot(
        self,
        round_id: Optional[int],
        state: Dict[str, Any],
        *,
        is_submission: bool,
        submission_just_closed: bool,
        is_evaluation: bool,
        finalisation_due: bool,
    ) -> None:
        """One [vv_op] line per step so operators can grep cadence gates vs Platform deadlines."""
        phase = str(state.get("round_phase", "submission"))
        now = int(time.time())
        sub_dl = int(state.get("submission_deadline_unix", 0) or 0)
        eval_dl = int(state.get("evaluation_deadline_unix", 0) or 0)
        reason = (
            f"platform_phase={phase} now_unix={now} "
            f"submission_deadline_unix={sub_dl} evaluation_deadline_unix={eval_dl} "
            f"submission_open={is_submission} submission_just_closed={submission_just_closed} "
            f"evaluation_window={is_evaluation} finalisation_due={finalisation_due}"
        )
        log_vv_debug(
            "cadence_routing",
            round_id=round_id,
            phase=phase,
            validator_hotkey=self._validator_hotkey(),
            reason=reason,
        )

    # ── Lifecycle steps ─────────────────────────────────────────────────────────

    async def step(self) -> None:
        """Execute one validator step based on current phase."""
        _cadence.reload()  # pick up subnet_settings.json / env-var changes without restart
        self._liveness.begin_step()
        try:
            self.metagraph.sync()
            await self._step_platform_mode()
        except Exception as exc:
            self._last_liveness_action = "step_exception"
            log_vv_error(
                "validator_step_failed",
                round_id=self._liveness_round_id,
                phase=self._liveness_phase,
                validator_hotkey=self._validator_hotkey(),
                reason=_classify_validator_exception(exc),
            )
            bt.logging.debug(traceback.format_exc())
            raise
        finally:
            self._liveness.record_step_finished(
                self._last_liveness_action,
                self._liveness_round_id,
                self._liveness_phase,
            )

    def _compute_prompt_voting_outcome(
        self, prompt_votes_data: Optional[Dict[str, Any]] = None
    ) -> Optional[PromptVotingDecision]:
        """
        Fetch prompt votes, apply mechanism-owned completion rules.
        Returns PromptVotingDecision when in prompt_voting; None otherwise.
        Validator relies entirely on subnet: deadline comes from platform (which
        stores what validator pushed). No local timer.

        Pass prompt_votes_data from the same step when already fetched (reused_in_step);
        otherwise performs a fresh fetch_prompt_votes (no validator-side cache).
        """
        if prompt_votes_data is not None:
            data = prompt_votes_data
            bt.logging.info(
                "[platform_read] fetch_prompt_votes source=reused_in_step purpose=prompt_voting_decision"
            )
        else:
            bt.logging.info(
                "[platform_read] fetch_prompt_votes source=fresh purpose=prompt_voting_decision"
            )
            data = fetch_prompt_votes(self.platform_api_url)
        if not data or data.get("phase") != "prompt_voting":
            return None
        prompts = data.get("prompts", [])
        miner_ids = list(data.get("minerIds", []))
        voted_miner_ids = list(data.get("votedMinerIds", []))
        voting_deadline_unix = data.get("votingDeadlineUnix")
        total_miners_override = data.get("totalMiners")
        # Quorum: previously allowed validator override via os.environ.get("QUORUM_RATIO").
        # Now uses platform-provided (subnet-owner config) or mechanism DEFAULT_QUORUM_RATIO only.
        qr = data.get("quorumRatio")
        quorum_ratio = float(qr) if qr is not None else DEFAULT_QUORUM_RATIO
        now_unix = int(time.time())

        deadline_reached = False
        if voting_deadline_unix is not None and now_unix >= int(voting_deadline_unix):
            deadline_reached = True

        decision = compute_prompt_voting_decision(
            prompts=prompts,
            miner_ids=miner_ids,
            voted_miner_ids=voted_miner_ids,
            deadline_reached=deadline_reached,
            quorum_ratio=quorum_ratio,
            now_unix=now_unix,
            deadline_unix=int(voting_deadline_unix) if voting_deadline_unix is not None else None,
            total_miners_override=int(total_miners_override) if total_miners_override is not None else None,
        )
        return decision

    def _log_consensus_failure(
        self,
        from_phase: str,
        to_phase: str,
        round_id: Optional[int],
        reason: str,
    ) -> None:
        """Structured log when quorum was required but not achieved — no unilateral bypass."""
        log_vv_warning(
            "consensus_failure",
            round_id=round_id,
            phase=f"{from_phase}->{to_phase}",
            validator_hotkey=self._validator_hotkey(),
            reason=f"quorum_not_met will_retry_next_loop detail={reason}",
        )

    def _log_consensus_success(
        self,
        from_phase: str,
        to_phase: str,
        round_id: Optional[int],
    ) -> None:
        log_vv_info(
            "consensus_success",
            round_id=round_id,
            phase=f"{from_phase}->{to_phase}",
            validator_hotkey=self._validator_hotkey(),
            reason="quorum_confirmed_platform_applied_transition",
        )

    async def _push_transition_via_consensus(
        self,
        from_phase: str,
        to_phase: str,
        round_id: Optional[int],
        payload: Optional[Dict[str, Any]],
        **lifecycle_kw: Any,
    ) -> Tuple[bool, str]:
        """
        Propose and vote on phase transition. When quorum confirmed, Platform applies it.

        Returns (True, "") on quorum success, or (False, reason) on failure.
        Callers must NOT fall back to direct push for the same transition.
        """
        if self._is_pending_validator:
            bt.logging.info(
                "[pending_gate] Skipping consensus proposal/vote — validator is PENDING "
                "quorum admission. Will participate once promoted to active at the next "
                "prompt_voting phase."
            )
            return False, "pending_validator_not_in_quorum"
        hotkey = self.wallet.hotkey.ss58_address if self.wallet else None
        if not hotkey:
            return False, "no_validator_hotkey"
        import asyncio
        ok, reason = await asyncio.to_thread(
            sync_from_checkpoint_and_propose_or_vote,
            self.platform_api_url,
            hotkey,
            from_phase,
            to_phase,
            round_id,
            payload,
        )
        if ok:
            self._platform_round_cache = None
            return True, ""
        return False, reason

    def _push_lifecycle(
        self,
        phase: str,
        round_id: Optional[int],
        restart_decision: Optional[Dict[str, Any]] = None,
        selected_prompt_id: Optional[str] = None,
        selected_prompt_computed: bool = False,
        transition_source: Optional[str] = "validator_produced",
        validator_completion: Optional[Dict[str, Any]] = None,
        canonical_narrative_progression: Optional[int] = None,
        scoring_outcome: Optional[Dict[str, Any]] = None,
        prompt_voting_outcome: Optional[Dict[str, Any]] = None,
        round_bootstrap: Optional[Dict[str, Any]] = None,
        prompt_voting_deadline_unix: Optional[int] = None,
        target_epoch_block: Optional[int] = None,
    ) -> LifecyclePushResult:
        """Push lifecycle state to platform — validator produces, platform consumes.

        Returns a structured LifecyclePushResult. ``accepted`` is True when the platform
        accepted the push. Callers that perform irreversible side-effects
        (e.g. writing the local finalisation marker) MUST check ``accepted``
        and skip those effects when it is False.
        """
        return push_lifecycle_to_platform(
            platform_api_url=self.platform_api_url,
            phase=phase,
            round_id=round_id,
            selected_prompt_id=selected_prompt_id,
            selected_prompt_computed=selected_prompt_computed,
            transition_source=transition_source,
            validator_completion=validator_completion,
            restart_decision=restart_decision,
            canonical_narrative_progression=canonical_narrative_progression,
            scoring_outcome=scoring_outcome,
            prompt_voting_outcome=prompt_voting_outcome,
            round_bootstrap=round_bootstrap,
            prompt_voting_deadline_unix=prompt_voting_deadline_unix,
            target_epoch_block=target_epoch_block,
            validator_hotkey=self.wallet.hotkey.ss58_address if self.wallet else None,
            db_path=self.db_path,
        )

    async def _handle_prompt_voting_outcome(
        self,
        decision: "PromptVotingDecision",
        selected_prompt_id: Optional[str],
        selected_prompt_computed: bool,
        completion_reason: Optional[str],
        no_selection_reason: Optional[str],
        phase: str,
        round_id: Optional[int],
    ) -> bool:
        """
        Execute the prompt-voting completion: attempt the consensus transition
        (prompt_voting -> submission) or record a no-selection outcome.

        Returns True if the calling step should abort (consensus waiting for quorum
        or a pending-gate guard fired); False to continue normally.
        """
        round_bootstrap: Optional[Dict[str, Any]] = None
        prompt_voting_outcome: Optional[Dict[str, Any]] = None
        transition_confirmed = False

        if selected_prompt_id:
            sub_unix, eval_unix = compute_round_deadlines()
            round_bootstrap = {
                "selectedPromptId": selected_prompt_id,
                "bootstrapReason": "prompt_voting",
                "source": "validator_produced",
                "transitionReason": completion_reason or "mechanism_complete",
                "completionReason": completion_reason,
                "quorumMet": decision.quorum_met,
                "deadlineReached": decision.deadline_reached,
                "totalMiners": decision.total_miners,
                "votedMiners": decision.voted_miners,
                "tieBroken": decision.tie_broken,
                "submissionDeadlineUnix": sub_unix,
                "evaluationDeadlineUnix": eval_unix,
            }
            payload = {"roundBootstrap": round_bootstrap}
            # Consensus-gated: prompt_voting -> submission (round bootstrap).
            ok, cons_reason = await self._push_transition_via_consensus(
                "prompt_voting", "submission", None, payload
            )
            if ok:
                self._log_consensus_success("prompt_voting", "submission", None)
                transition_confirmed = True
                bt.logging.info(
                    "[phase_progression] prompt_voting -> submission (consensus) "
                    "selected_prompt_id=%s submission_deadline_unix=%s "
                    "evaluation_deadline_unix=%s completion_reason=%s",
                    selected_prompt_id, sub_unix, eval_unix, completion_reason or "",
                )
                log_vv_info(
                    "prompt_voting_complete",
                    round_id=None,
                    phase="prompt_voting->submission",
                    validator_hotkey=self._validator_hotkey(),
                    reason=(
                        f"path=consensus selected_prompt_id={selected_prompt_id} "
                        f"completion_reason={completion_reason}"
                    ),
                )
                log_evidence(
                    "validator", "lifecycle_transition",
                    round_id=None, phase="prompt_voting->submission",
                    action="prompt_voting_complete",
                    reason=f"consensus path selected_prompt_id={selected_prompt_id}",
                    validator_hotkey=self._validator_hotkey(),
                )
                self._emit_validator_event(
                    EVENT_PROMPT_VOTING_COMPLETE, None,
                    {"path": "consensus", "selected_prompt_id": selected_prompt_id,
                     "completion_reason": completion_reason},
                )
                log_vv_info(
                    "round_bootstrap",
                    round_id=None, phase="prompt_voting->submission",
                    validator_hotkey=self._validator_hotkey(),
                    reason=(
                        f"path=consensus selected_prompt_id={selected_prompt_id} "
                        f"submissionDeadlineUnix={sub_unix} evaluationDeadlineUnix={eval_unix}"
                    ),
                )
                log_evidence(
                    "validator", "lifecycle_transition",
                    round_id=None, phase="prompt_voting->submission",
                    action="round_bootstrap",
                    reason=(
                        f"consensus submission_deadline_unix={sub_unix} "
                        f"evaluation_deadline_unix={eval_unix}"
                    ),
                    validator_hotkey=self._validator_hotkey(),
                )
                self._emit_validator_event(
                    EVENT_ROUND_BOOTSTRAP, None,
                    {"path": "consensus", "selected_prompt_id": selected_prompt_id,
                     "submission_deadline_unix": sub_unix, "evaluation_deadline_unix": eval_unix},
                )
            else:
                self._log_consensus_failure("prompt_voting", "submission", None, cons_reason)
                self._liveness_note("consensus_retry_next_loop", round_id=None, phase="prompt_voting")
                return True  # abort step — consensus waiting for quorum
        else:
            prompt_voting_outcome = {
                "selectedPromptId": "",
                "advanceToRound": False,
                "source": "validator_produced",
                "transitionReason": completion_reason,
                "completionReason": completion_reason,
                "noSelectionReason": no_selection_reason,
            }
            log_vv_info(
                "prompt_voting_complete",
                round_id=None, phase="prompt_voting",
                validator_hotkey=self._validator_hotkey(),
                reason=(
                    f"no_selection no_selection_reason={no_selection_reason} "
                    f"completion_reason={completion_reason}"
                ),
            )
            self._emit_validator_event(
                EVENT_PROMPT_VOTING_COMPLETE, None,
                {"outcome": "no_selection", "no_selection_reason": no_selection_reason,
                 "completion_reason": completion_reason},
            )

        if not transition_confirmed:
            if self._is_pending_validator and round_bootstrap is not None:
                bt.logging.info(
                    "[pending_gate] Skipping round bootstrap push — "
                    "validator is PENDING quorum admission. "
                    "An active validator will advance the round."
                )
                return True  # abort step
            # When no prompts submitted by miners, reset countdown
            new_deadline_unix = None
            if no_selection_reason == NoSelectionReason.NO_PROMPTS.value:
                new_deadline_unix = int(time.time()) + _cadence.PROMPT_VOTING_WINDOW_SEC
                log_vv_info(
                    "prompt_voting_deadline_set",
                    round_id=None, phase="prompt_voting",
                    validator_hotkey=self._validator_hotkey(),
                    reason=(
                        f"reset_after_no_prompts votingDeadlineUnix={new_deadline_unix} "
                        f"window_sec={_cadence.PROMPT_VOTING_WINDOW_SEC}"
                    ),
                )
            lr_pv = self._push_lifecycle(
                phase, round_id,
                selected_prompt_id=selected_prompt_id,
                selected_prompt_computed=selected_prompt_computed,
                round_bootstrap=round_bootstrap,
                prompt_voting_outcome=prompt_voting_outcome,
                prompt_voting_deadline_unix=new_deadline_unix,
            )
            if round_bootstrap is not None and round_bootstrap.get("bootstrapReason") == "prompt_voting":
                if lr_pv.accepted:
                    log_vv_success(
                        "phase_transition",
                        round_id=lr_pv.platform_round_id_after_commit or lr_pv.round_id,
                        phase="prompt_voting->submission",
                        validator_hotkey=self._validator_hotkey(),
                        reason=(
                            f"selected_prompt_id={round_bootstrap.get('selectedPromptId')} "
                            f"platform_phase={lr_pv.platform_phase_after_commit or lr_pv.phase} "
                            f"lifecycle_version={lr_pv.lifecycle_version}"
                        ),
                    )
                else:
                    log_vv_warning(
                        "phase_transition_rejected",
                        round_id=None, phase="prompt_voting->submission",
                        validator_hotkey=self._validator_hotkey(),
                        reason=(
                            f"platform rejected roundBootstrap: {lr_pv.reason} "
                            f"retryable={lr_pv.retryable}"
                        ),
                    )

        return False  # continue step normally

    async def _step_platform_mode(self) -> None:
        """Platform mode: use Platform API for phase and cadence."""

        # On first successful step, log a full sync status so operators know exactly
        # where the network is and what this validator should do.
        if not self._startup_sync_logged:
            self._log_startup_sync()
            self._startup_sync_logged = True

        # Pending gate: re-poll every 3 minutes to catch promotion / re-pending.
        # Time-based so the interval is stable regardless of step duration.
        _pending_poll_interval = 180.0  # seconds
        if time.monotonic() - self._last_pending_check_mono >= _pending_poll_interval:
            self._last_pending_check_mono = time.monotonic()
            import asyncio
            hotkey = self._validator_hotkey()
            sync_check = await asyncio.to_thread(
                fetch_validator_sync_status, self.platform_api_url, validator_hotkey=hotkey
            )
            if sync_check:
                jv_check = sync_check.get("joiningValidator", {})
                av_check = sync_check.get("activeValidators", {})
                cs_check = sync_check.get("currentState", {})
                was_pending = self._is_pending_validator
                self._is_pending_validator = jv_check.get("isPending", False)
                if was_pending and not self._is_pending_validator:
                    bt.logging.info(
                        "[pending_gate] ✓ PROMOTED — your validator is now ACTIVE in the quorum. "
                        f"hotkey={hotkey[:8]}... You are now counted toward consensus."
                    )
                    log_evidence(
                        "validator",
                        "pending_gate_promoted",
                        action="quorum_promotion",
                        reason="prompt_voting_gate_passed",
                        validator_hotkey=hotkey,
                    )
                elif self._is_pending_validator:
                    # Still pending — log current network context so operators know where things stand.
                    bt.logging.info(
                        "[pending_gate] ⏳ Still PENDING quorum admission. "
                        f"network phase={cs_check.get('phase', '?')} "
                        f"round={cs_check.get('roundId', 'none')} "
                        f"active_validators={av_check.get('count', '?')} "
                        f"quorum_required={av_check.get('quorumRequired', '?')}. "
                        "Promotion happens at next prompt_voting phase (requires ≥1 active critic). "
                        "Keep pushing lifecycle heartbeats to stay visible."
                    )

        snap = self._load_platform_snapshot_for_step()
        if snap is None:
            self._liveness_note("platform_snapshot_unavailable", None, "-", eval_touch=True)
            return
        current, state, _read_mode = snap
        self._reconcile_platform_lifecycle_state(current, state)
        round_id = current.get("roundId") if current else None
        bt.logging.debug(
            f"[platform_read] step_routing snapshot={_read_mode} roundId={round_id}"
        )
        phase = state.get("round_phase", "submission") if state else "prompt_voting"
        self._liveness_note(
            f"platform_routed:{phase}",
            round_id=round_id,
            phase=phase,
            eval_touch=True,
        )

        if not state:
            if current:
                phase = current.get("phase") or ("prompt_voting" if not round_id else "finalised")
                selected_prompt_id, selected_prompt_computed = None, False
                round_bootstrap = None
                prompt_voting_outcome = None
                if not round_id and phase == "prompt_voting":
                    # One fresh prompt-votes read for deadline gate + completion (no stale snapshot).
                    bt.logging.info(
                        "[platform_read] fetch_prompt_votes source=fresh purpose=prompt_voting_step"
                    )
                    pv_data = fetch_prompt_votes(self.platform_api_url)

                    # Push a lightweight prompt_voting heartbeat on a timer so the platform
                    # registers this validator in the heartbeat table. This fires unconditionally
                    # (regardless of deadline state or vote thresholds) so a new validator always
                    # becomes visible even when a deadline was previously set or has since expired.
                    _pv_heartbeat_interval = 60.0  # seconds
                    if time.monotonic() - self._last_pv_heartbeat_mono >= _pv_heartbeat_interval:
                        self._last_pv_heartbeat_mono = time.monotonic()
                        self._push_lifecycle(
                            "prompt_voting",
                            None,
                            transition_source="validator_produced",
                        )

                    if pv_data and pv_data.get("phase") == "prompt_voting":
                        # Only arm when the platform has never stored a deadline. If the window expired,
                        # votingDeadlineUnix must still be the past unix ts (not null) so
                        # _compute_prompt_voting_outcome sets deadline_reached — otherwise we would
                        # push a fresh window every step forever.
                        if pv_data.get("votingDeadlineUnix") is None:
                            total_miners = pv_data.get("totalMiners")
                            voted_miner_ids = pv_data.get("votedMinerIds") or []
                            voted_count = len(voted_miner_ids)
                            if (
                                total_miners is not None
                                and total_miners >= _cadence.MINER_COUNT_FOR_COUNTDOWN
                                and voted_count >= _cadence.MIN_VOTED_MINERS_FOR_COUNTDOWN
                            ):
                                deadline_unix = int(time.time()) + _cadence.PROMPT_VOTING_WINDOW_SEC
                                if self._is_pending_validator:
                                    bt.logging.info(
                                        "[pending_gate] Skipping prompt_voting deadline set — "
                                        "validator is PENDING quorum admission. "
                                        "An active validator will set the deadline."
                                    )
                                else:
                                    self._push_lifecycle(
                                        "prompt_voting",
                                        None,
                                        prompt_voting_deadline_unix=deadline_unix,
                                        transition_source="validator_produced",
                                    )
                                    log_vv_success(
                                        "prompt_voting_deadline_set",
                                    round_id=None,
                                    phase="prompt_voting",
                                    validator_hotkey=self._validator_hotkey(),
                                    reason=(
                                        f"votingDeadlineUnix={deadline_unix} window_sec={_cadence.PROMPT_VOTING_WINDOW_SEC} "
                                        f"totalMiners={total_miners} voted={voted_count}"
                                    ),
                                )
                            else:
                                bt.logging.debug(
                                    f"Prompt voting: waiting for {_cadence.MINER_COUNT_FOR_COUNTDOWN} miners and "
                                    f"{_cadence.MIN_VOTED_MINERS_FOR_COUNTDOWN} votes (totalMiners={total_miners}, voted={voted_count})"
                                )
                    decision = self._compute_prompt_voting_outcome(
                        pv_data if pv_data and pv_data.get("phase") == "prompt_voting" else None
                    )
                    if decision and decision.complete:
                        selected_prompt_id = decision.selected_prompt_id
                        selected_prompt_computed = decision.selected_prompt_id is not None
                        completion_reason = (
                            decision.completion_reason.value if decision.completion_reason else None
                        )
                        no_selection_reason = (
                            decision.no_selection_reason.value if decision.no_selection_reason else None
                        )
                        if selected_prompt_id:
                            log_vv_success(
                                "prompt_voting_winner_selected",
                                round_id=None,
                                phase="prompt_voting",
                                validator_hotkey=self._validator_hotkey(),
                                reason=(
                                    f"selected_prompt_id={selected_prompt_id} "
                                    f"completion_reason={completion_reason} "
                                    f"voted={decision.voted_miners}/{decision.total_miners}"
                                ),
                            )
                        else:
                            log_vv_warning(
                                "prompt_voting_no_selection",
                                round_id=None,
                                phase="prompt_voting",
                                validator_hotkey=self._validator_hotkey(),
                                reason=(
                                    f"no_selection_reason={no_selection_reason} "
                                    f"completion_reason={completion_reason} "
                                    f"voted={decision.voted_miners}/{decision.total_miners}"
                                ),
                            )
                        if await self._handle_prompt_voting_outcome(
                            decision,
                            selected_prompt_id,
                            selected_prompt_computed,
                            completion_reason,
                            no_selection_reason,
                            phase,
                            round_id,
                        ):
                            return
                self._liveness_note(
                    "prompt_voting_step_complete",
                    round_id=round_id,
                    phase=phase,
                )
                return  # Prompt voting handled; no active round state to fetch
            if not current:
                bt.logging.warning(
                    "Could not fetch Platform round state — skipping step. "
                    "Check: (1) Platform running at PLATFORM_API_URL, (2) Platform has AUTH_MODE=live, "
                    "HTTP_BRIDGE_URL, VIVIDVERSE_NETUID=210, (3) Bridge running with HTTP_BRIDGE_MOCK=0."
                )
            elif round_id is not None:
                bt.logging.warning(
                    f"Platform reports round {round_id} but fetch_round_state failed — "
                    "check /api/subnet/rounds/{id}/state is reachable."
                )
            self._liveness_note("platform_round_state_unavailable", round_id=round_id, phase="-")
            return

        selected_prompt_id = state.get("selected_prompt_id")

        (
            is_submission,
            submission_just_closed,
            is_evaluation,
            finalisation_due,
        ) = self._platform_phase_checks(state)

        self._log_platform_cadence_snapshot(
            round_id,
            state,
            is_submission=is_submission,
            submission_just_closed=submission_just_closed,
            is_evaluation=is_evaluation,
            finalisation_due=finalisation_due,
        )

        round_mgr: Dict[str, Any] = state

        if submission_just_closed:
            # Require at least one submission to advance. If none, extend deadline (restart timer).
            submission_count = state.get("submission_count", -1)
            if submission_count == 0:
                sub_unix, eval_unix = compute_round_deadlines()
                round_bootstrap = {
                    "submissionDeadlineUnix": sub_unix,
                    "evaluationDeadlineUnix": eval_unix,
                }
                log_vv_info(
                    "submission_closed",
                    round_id=round_id,
                    phase="submission",
                    validator_hotkey=self._validator_hotkey(),
                    reason="submission_count=0 extending_deadlines restart_timer_same_phase",
                )
                self._emit_validator_event(
                    EVENT_SUBMISSION_WINDOW_CLOSED,
                    round_id,
                    {
                        "variant": "extend_deadline_same_phase",
                        "submission_count": 0,
                    },
                )
                if self._is_pending_validator:
                    bt.logging.info(
                        "[pending_gate] Skipping submission deadline extension — "
                        "validator is PENDING quorum admission. "
                        "An active validator will handle the extension."
                    )
                else:
                    self._push_lifecycle(
                        "submission",
                        round_id,
                        round_bootstrap=round_bootstrap,
                        selected_prompt_id=selected_prompt_id,
                        selected_prompt_computed=False,
                        transition_source="validator_produced",
                    )
                self._platform_round_cache = None
                self._liveness_note(
                    "platform_submission_closed_extend_deadline",
                    round_id=round_id,
                    phase="submission",
                )
            else:
                log_vv_info(
                    "submission_closed",
                    round_id=round_id,
                    phase="submission",
                    validator_hotkey=self._validator_hotkey(),
                    reason=f"advancing_to_evaluation submission_count={submission_count}",
                )
                self._emit_validator_event(
                    EVENT_SUBMISSION_WINDOW_CLOSED,
                    round_id,
                    {
                        "variant": "advance_to_evaluation",
                        "submission_count": submission_count,
                    },
                )
                ok, cons_reason = await self._push_transition_via_consensus(
                    "submission", "evaluation", round_id, None
                )
                if ok:
                    self._log_consensus_success(
                        "submission", "evaluation", round_id
                    )
                    log_vv_success(
                        "phase_transition",
                        round_id=round_id,
                        phase="submission->evaluation",
                        validator_hotkey=self._validator_hotkey(),
                        reason="consensus_confirmed",
                    )
                    log_evidence(
                        "validator",
                        "lifecycle_transition",
                        round_id=round_id,
                        phase="submission->evaluation",
                        action="submission_to_evaluation",
                        reason="consensus_confirmed",
                        validator_hotkey=self._validator_hotkey(),
                    )
                else:
                    self._log_consensus_failure(
                        "submission", "evaluation", round_id, cons_reason
                    )
                    self._liveness_note(
                        "consensus_retry_next_loop",
                        round_id=round_id,
                        phase="submission",
                    )
                    self._platform_round_cache = None
                    return
                self._liveness_note(
                    "platform_submission_closed_to_evaluation",
                    round_id=round_id,
                    phase="submission->evaluation",
                )
                self._platform_round_cache = None
        elif is_submission:
            if self._last_phase_announced != (round_id, "submission"):
                self._last_phase_announced = (round_id, "submission")
                log_vv_success(
                    "phase_entered",
                    round_id=round_id,
                    phase="submission",
                    validator_hotkey=self._validator_hotkey(),
                    reason="submission_window_open_broadcasting_to_miners",
                )
            self._push_phase_heartbeat("submission", round_id, state, selected_prompt_id=selected_prompt_id)
            log_vv_debug(
                "submission_phase_active",
                round_id=round_id,
                phase="submission",
                validator_hotkey=self._validator_hotkey(),
                reason="awaiting_platform_submissions",
            )
            self._liveness_note(
                "platform_submission_active",
                round_id=round_id,
                phase="submission",
            )
        elif is_evaluation:
            if self._last_phase_announced != (round_id, "evaluation"):
                self._last_phase_announced = (round_id, "evaluation")
                log_vv_success(
                    "phase_entered",
                    round_id=round_id,
                    phase="evaluation",
                    validator_hotkey=self._validator_hotkey(),
                    reason="awaiting_critic_scores_on_platform",
                )
            self._push_phase_heartbeat("evaluation", round_id, state, selected_prompt_id=selected_prompt_id)
            log_vv_debug(
                "evaluation_active",
                round_id=round_id,
                phase="evaluation",
                validator_hotkey=self._validator_hotkey(),
                reason="waiting_for_platform_critic_scores",
            )
            log_evidence(
                "validator",
                "lifecycle_transition",
                round_id=round_id,
                phase="evaluation",
                action="evaluation_started",
                reason="waiting_for_platform_critic_scores",
                validator_hotkey=self._validator_hotkey(),
            )
            self._emit_validator_event(
                EVENT_EVALUATION_STARTED,
                round_id,
                {"mode": "platform"},
            )
            # Periodic in-progress snapshot — emit every 90 steps (~15 min at default interval)
            # so judges have timestamped evidence even mid-round before finalisation.
            _snap_key = round_id if round_id is not None else -1
            self._eval_snapshot_step[_snap_key] = self._eval_snapshot_step.get(_snap_key, 0) + 1
            if self._eval_snapshot_step[_snap_key] % 90 == 1:
                _sub_count = state.get("submission_count") if state else None
                _eval_dl = state.get("evaluation_deadline_unix") if state else None
                log_round_in_progress_snapshot(
                    round_id=round_id,
                    validator_hotkey=self._validator_hotkey(),
                    phase="evaluation",
                    submission_count=_sub_count,
                    evaluation_deadline_unix=_eval_dl,
                )
                self._emit_validator_event(
                    "in_progress_snapshot",
                    round_id,
                    {
                        "phase": "evaluation",
                        "submission_count": _sub_count,
                        "evaluation_deadline_unix": _eval_dl,
                        "step": self._eval_snapshot_step[_snap_key],
                    },
                )
            self._liveness_note(
                "platform_evaluation_active",
                round_id=round_id,
                phase="evaluation",
            )
        elif finalisation_due:
            if self._last_phase_announced != (round_id, "finalisation"):
                self._last_phase_announced = (round_id, "finalisation")
                log_vv_success(
                    "phase_entered",
                    round_id=round_id,
                    phase="finalisation",
                    validator_hotkey=self._validator_hotkey(),
                    reason="evaluation_deadline_passed_invoking_finalise_round",
                )
            log_vv_info(
                "cadence_finalisation",
                round_id=round_id,
                phase="evaluation",
                validator_hotkey=self._validator_hotkey(),
                reason=(
                    "evaluation_deadline_passed_and_phase_still_evaluation "
                    "invoking_finalise_round_platform (tempo_and_quorum_gates_inside)"
                ),
            )
            # Push a heartbeat so the platform sees this validator as active even while
            # finalisation is blocked (e.g. waiting for critic quorum). Without this,
            # the validator never pushes a lifecycle in the finalisation_due branch and
            # its heartbeat expires, causing Validators: 0 / frozen state.
            self._push_phase_heartbeat("evaluation", round_id, state, selected_prompt_id=selected_prompt_id)
            await self._finalise_round_platform(round_mgr)

    def _push_phase_heartbeat(
        self,
        phase: str,
        round_id: Optional[int],
        state: Optional[Dict[str, Any]],
        *,
        selected_prompt_id: Optional[str] = None,
    ) -> None:
        """Push a heartbeat lifecycle for the active phase (submission or evaluation).

        This is the steady-state push that fires every step while the round is
        in a waiting phase.  It carries no irreversible side effects, so the
        return value is intentionally discarded.
        """
        self._push_lifecycle(
            phase, round_id,
            selected_prompt_id=selected_prompt_id,
            selected_prompt_computed=False,
            round_bootstrap=deadline_only_round_bootstrap(state),
        )

    async def _finalise_round_platform(
        self, round_mgr: Dict[str, Any]
    ) -> None:
        """Delegate to vividverse.validator.finalise.finalise_round_platform."""
        await finalise_round_platform(self, round_mgr)

    # ── Main loop ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Run the validator main loop."""
        import asyncio

        _nw = getattr(self.config.subtensor, "network", None) or "-"
        _hk = self._validator_hotkey()
        _hk_disp = (
            f"{_hk[:16]}..."
            if _hk and _hk != "-" and len(_hk) > 16
            else _hk
        )
        _plat = self.platform_api_url
        self._liveness.start_background_thread()
        _deg = (
            f"{VALIDATOR_STEP_INTERVAL_DEGRADED_SEC:.1f}s"
            if VALIDATOR_STEP_INTERVAL_DEGRADED_SEC > 0
            else "off"
        )
        bt.logging.info(
            f"Step interval={VALIDATOR_STEP_INTERVAL_SEC:.1f}s (VALIDATOR_STEP_INTERVAL_SEC); "
            f"degraded_poll={_deg} when Platform snapshot fails (VALIDATOR_STEP_INTERVAL_DEGRADED_SEC)."
        )

        # Prefetch before banner so phase= matches UI (not local round_mgr default).
        self._bootstrap_platform_display_phase()

        _phase = self._phase_display_for_operator()
        print(
            f"\n{'=' * 60}\n"
            f"  Vividverse validator\n"
            f"  netuid={self.config.netuid}  network={_nw}\n"
            f"  hotkey={_hk_disp}\n"
            f"  phase={_phase}\n"
            f"  platform={_plat}\n"
            f"  Starting main loop (first step may take several seconds)…\n"
            f"{'=' * 60}\n",
            flush=True,
        )
        bt.logging.info(
            f"Validator starting main loop (phase={_phase}, netuid={self.config.netuid})..."
        )

        async def main_loop() -> None:
            try:
                while True:
                    await self.step()
                    if not self._printed_first_step_ok:
                        self._printed_first_step_ok = True
                        _ph_done = self._phase_display_for_operator()
                        bt.logging.info(
                            "[Vividverse] Validator: first step completed — "
                            f"netuid={self.config.netuid} phase={_ph_done} "
                            f"main loop active (Ctrl+C to stop)"
                        )
                        print(
                            f"[Vividverse] Validator: first step completed — "
                            f"phase={_ph_done} — main loop is running. Ctrl+C to stop.\n",
                            flush=True,
                        )
                    sleep_sec = VALIDATOR_STEP_INTERVAL_SEC
                    if (
                        self._platform_consecutive_failures > 0
                        and VALIDATOR_STEP_INTERVAL_DEGRADED_SEC > 0
                    ):
                        sleep_sec = min(sleep_sec, VALIDATOR_STEP_INTERVAL_DEGRADED_SEC)
                    # Fast-poll when tempo is the only remaining gate — don't miss the 12s window.
                    if self._in_tempo_wait and VALIDATOR_STEP_INTERVAL_TEMPO_WAIT_SEC > 0:
                        sleep_sec = min(sleep_sec, VALIDATOR_STEP_INTERVAL_TEMPO_WAIT_SEC)
                    await asyncio.sleep(sleep_sec)
            except asyncio.CancelledError:
                pass

        try:
            try:
                asyncio.run(main_loop())
            except KeyboardInterrupt:
                bt.logging.info("Shutting down...")
        finally:
            self._liveness.shutdown()
            register_liveness_tracker(None)


def get_config():
    """Parse command line arguments and return config."""
    parser = argparse.ArgumentParser(description="Vividverse Validator")

    # Bittensor standard args
    _env_netuid = os.environ.get("VALIDATOR_NETUID") or os.environ.get("VIVIDVERSE_NETUID")
    _default_netuid = int(_env_netuid) if _env_netuid else None
    parser.add_argument("--netuid", type=int, required=_default_netuid is None,
                        default=_default_netuid, help="Subnet UID")
    _env_network = os.environ.get("VALIDATOR_SUBTENSOR_NETWORK")
    parser.add_argument("--subtensor.network", default=_env_network or "finney", help="Network name")
    parser.add_argument("--subtensor.chain_endpoint", default=None, help="Chain endpoint")
    parser.add_argument("--wallet.name", default="validator", help="Wallet coldkey name")
    parser.add_argument("--wallet.hotkey", default="default", help="Wallet hotkey name")
    parser.add_argument("--db_path", default="vividverse.db", help="Database path")
    parser.add_argument(
        "--platform-api-url",
        default=None,
        help="Platform API URL for subnet integration (uses PLATFORM_API_URL env if not set)",
    )
    parser.add_argument("--logging.debug", action="store_true", help="Enable debug logging")

    config = bt.Config(parser)
    return config


def main():
    config = get_config()
    bt.logging(config=config)

    validator = Validator(config)
    validator.run()


if __name__ == "__main__":
    main()