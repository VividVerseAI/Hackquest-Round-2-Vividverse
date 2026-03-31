"""
Lightweight liveness / heartbeat tracking for the Vividverse validator.

Tracks step count, last activity (monotonic), last state-evaluation time, and
optional flags so a background thread can detect stalls without blocking async.

ENV:
  VV_LIVENESS_ENABLED       — "1"/"true" to enable (default: enabled)
  VV_LIVENESS_STALL_SEC     — no activity for this long => stall error (default: 900)
  VV_LIVENESS_HEARTBEAT_SEC — checker wake interval (default: 30)
  VV_LIVENESS_HTTP_GRACE_SEC — extra grace after HTTP retry sleep (default: 15)

Stall logging is error-severity [vv_op] action=liveness_stall. No auto-restart.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional, Union

import bittensor as bt

from vividverse.utils.ops_log import log_vv_error

_active: Optional["LivenessTracker"] = None


def register_liveness_tracker(tracker: Optional["LivenessTracker"]) -> None:
    """Process-wide hook for http_retry and other helpers (optional)."""
    global _active
    _active = tracker


def notify_http_retry_sleep(duration_sec: float) -> None:
    """Call before blocking sleep in HTTP retry so stall checks ignore that window."""
    t = _active
    if t is not None and duration_sec > 0:
        t.extend_http_grace(duration_sec)


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class LivenessTracker:
    """
    Thread-safe activity tracking. The validator records progress from the async
    thread; a daemon thread periodically checks for stalls.
    """

    def __init__(
        self,
        *,
        stall_threshold_sec: float,
        heartbeat_interval_sec: float,
        validator_hotkey: str,
        enabled: bool = True,
        http_grace_extra_sec: Optional[float] = None,
    ) -> None:
        self.stall_threshold_sec = max(30.0, stall_threshold_sec)
        self.heartbeat_interval_sec = max(5.0, heartbeat_interval_sec)
        self._validator_hotkey = validator_hotkey
        self.enabled = enabled
        self._http_grace_extra_sec = (
            http_grace_extra_sec
            if http_grace_extra_sec is not None
            else _env_float("VV_LIVENESS_HTTP_GRACE_SEC", 15.0)
        )

        self._lock = threading.Lock()
        self._step_seq = 0
        self._last_activity_mono = time.monotonic()
        self._last_eval_mono = time.monotonic()
        self._last_wall_ts = time.time()
        self._last_action = "init"
        self._last_round_id: Optional[Union[int, str]] = None
        self._last_phase = "-"
        self._tempo_wait_active = False
        self._tempo_wait_reason = ""
        self._http_grace_until_mono = 0.0
        self._stall_logged = False

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- Recording (safe from async thread; short lock) ---

    def begin_step(self) -> None:
        """Call at the start of each validator step (clears per-step tempo flag)."""
        with self._lock:
            self._step_seq += 1
            self._tempo_wait_active = False
            self._tempo_wait_reason = ""

    def touch_eval(
        self,
        action: str,
        *,
        round_id: Optional[Union[int, str]] = None,
        phase: Optional[str] = None,
    ) -> None:
        """Meaningful state evaluation (routing, phase checks)."""
        with self._lock:
            self._last_eval_mono = time.monotonic()
            self._touch_unlocked(action, round_id, phase)

    def record_step_finished(
        self,
        action: str,
        round_id: Optional[Union[int, str]],
        phase: Optional[str],
    ) -> None:
        """Call in step() finally: one heartbeat per completed step cycle."""
        with self._lock:
            self._touch_unlocked(action, round_id, phase)
            self._stall_logged = False  # new activity clears stall episode

    def mark_tempo_wait(
        self,
        *,
        round_id: Optional[Union[int, str]],
        phase: Optional[str],
        reason: str,
    ) -> None:
        """
        Waiting for Bittensor tempo before finalisation — valid idle, not a hang.
        Refreshes activity and suppresses stall while flag is set until next begin_step.
        """
        with self._lock:
            self._tempo_wait_active = True
            self._tempo_wait_reason = reason
            self._touch_unlocked(
                "healthy_wait:tempo",
                round_id,
                phase,
            )

    def extend_http_grace(self, sleep_sec: float) -> None:
        """Transient HTTP retry/backoff — do not treat as stuck during this window."""
        if sleep_sec <= 0:
            return
        with self._lock:
            until = time.monotonic() + sleep_sec + self._http_grace_extra_sec
            if until > self._http_grace_until_mono:
                self._http_grace_until_mono = until

    def _touch_unlocked(
        self,
        action: str,
        round_id: Optional[Union[int, str]],
        phase: Optional[str],
    ) -> None:
        self._last_activity_mono = time.monotonic()
        self._last_wall_ts = time.time()
        self._last_action = action
        if round_id is not None:
            self._last_round_id = round_id
        if phase is not None and phase != "":
            self._last_phase = phase

    # --- Background checker ---

    def start_background_thread(self) -> None:
        if not self.enabled:
            bt.logging.info("[liveness] disabled (VV_LIVENESS_ENABLED=0)")
            return
        if self._thread is not None and self._thread.is_alive():
            return

        def _run() -> None:
            while not self._stop.wait(timeout=self.heartbeat_interval_sec):
                self._check_stall()

        self._thread = threading.Thread(
            target=_run,
            name="vividverse-liveness",
            daemon=True,
        )
        self._thread.start()
        bt.logging.info(
            f"[liveness] background checker started "
            f"stall_sec={self.stall_threshold_sec:.0f} interval_sec={self.heartbeat_interval_sec:.0f}"
        )

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _check_stall(self) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        with self._lock:
            if now < self._http_grace_until_mono:
                return
            if self._tempo_wait_active:
                return
            idle = now - self._last_activity_mono
            if idle <= self.stall_threshold_sec:
                return
            if self._stall_logged:
                return
            step_seq = self._step_seq
            action = self._last_action
            rid = self._last_round_id
            phase = self._last_phase
            idle_s = int(idle)
            eval_idle_s = int(now - self._last_eval_mono)
            self._stall_logged = True

        log_vv_error(
            "liveness_stall",
            round_id=rid,
            phase=phase,
            validator_hotkey=self._validator_hotkey,
            reason=(
                f"category=true_stuck no_activity_s={idle_s} since_last_eval_s={eval_idle_s} "
                f"threshold_s={int(self.stall_threshold_sec)} step_seq={step_seq} "
                f"last_successful_action={action} "
                f"(healthy_tempo_wait http_retry_grace suppress stall)"
            ),
        )


def build_tracker_from_env(validator_hotkey: str) -> LivenessTracker:
    """Construct tracker using VV_LIVENESS_* environment variables."""
    enabled = _env_bool("VV_LIVENESS_ENABLED", True)
    stall = _env_float("VV_LIVENESS_STALL_SEC", 900.0)
    hb = _env_float("VV_LIVENESS_HEARTBEAT_SEC", 30.0)
    return LivenessTracker(
        stall_threshold_sec=stall,
        heartbeat_interval_sec=hb,
        validator_hotkey=validator_hotkey,
        enabled=enabled,
    )
