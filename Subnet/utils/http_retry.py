"""
vividverse/utils/http_retry.py

Bounded exponential backoff for fragile Platform HTTP calls from the validator.

WHY: Transient network errors, cold starts, and brief Platform load should not
force a full validator step to fail immediately. Retries are capped so the
validator loop never blocks for long in a single step.

ENV (defaults are conservative; total sleep is typically under a few seconds):
  PLATFORM_HTTP_RETRY_MAX_ATTEMPTS   — attempts per call (default 4)
  PLATFORM_HTTP_RETRY_INITIAL_MS     — first backoff after failure (default 200)
  PLATFORM_HTTP_RETRY_MAX_MS         — cap per sleep (default 2500)
  PLATFORM_HTTP_RETRY_MAX_TOTAL_MS   — max cumulative sleep across retries (default 8000)
  PLATFORM_HTTP_RETRY_JITTER_RATIO   — multiplicative jitter on each sleep (0 = off; e.g. 0.12 spreads delay)
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Callable, Optional, Tuple

import bittensor as bt

from vividverse.utils.liveness import notify_http_retry_sleep
from vividverse.utils.ops_log import log_vv_error, log_vv_info, log_vv_warning


def _retry_config() -> tuple[int, float, float, float]:
    max_attempts = max(1, int(os.environ.get("PLATFORM_HTTP_RETRY_MAX_ATTEMPTS", "4")))
    initial_ms = max(1.0, float(os.environ.get("PLATFORM_HTTP_RETRY_INITIAL_MS", "200")))
    max_ms = max(initial_ms, float(os.environ.get("PLATFORM_HTTP_RETRY_MAX_MS", "2500")))
    max_total_ms = max(0.0, float(os.environ.get("PLATFORM_HTTP_RETRY_MAX_TOTAL_MS", "8000")))
    return max_attempts, initial_ms, max_ms, max_total_ms


def retry_platform_http(
    fn_name: str,
    round_id: Optional[int],
    attempt_fn: Callable[[], Tuple[bool, Any, str]],
) -> Any:
    """
    Run attempt_fn until it returns ok=True or retries are exhausted.

    attempt_fn must return (ok, value, failure_reason). When ok is False, value is
    typically None / False and failure_reason is logged.

    Logs structured [vv_op] lines (platform_http_retry_* actions) with round_id and reasons.
    Does not loop forever; bounded by max_attempts and optional total sleep budget.
    """
    max_attempts, initial_ms, cap_ms, max_total_ms = _retry_config()
    total_slept_ms = 0.0
    last_val: Any = None
    last_reason = ""

    for attempt in range(max_attempts):
        ok, val, reason = attempt_fn()
        last_val = val
        last_reason = reason
        if ok:
            if attempt > 0:
                log_vv_info(
                    "platform_http_retry_recovered",
                    round_id=round_id,
                    phase="-",
                    validator_hotkey="-",
                    reason=f"fn={fn_name} attempt={attempt + 1}",
                )
            return val
        if attempt < max_attempts - 1:
            sleep_ms = min(cap_ms, initial_ms * (2**attempt))
            jitter_ratio = float(os.environ.get("PLATFORM_HTTP_RETRY_JITTER_RATIO", "0.0"))
            if jitter_ratio > 0:
                sleep_ms = min(
                    cap_ms,
                    sleep_ms * (1.0 + random.random() * jitter_ratio),
                )
            if max_total_ms > 0 and total_slept_ms + sleep_ms > max_total_ms:
                sleep_ms = max(0.0, max_total_ms - total_slept_ms)
            if sleep_ms <= 0:
                log_vv_error(
                    "platform_http_retry_exhausted",
                    round_id=round_id,
                    phase="-",
                    validator_hotkey="-",
                    reason=f"fn={fn_name} attempts={attempt + 1}/{max_attempts} last={reason} cap_ms={max_total_ms}",
                )
                return last_val
            log_vv_warning(
                "platform_http_retry_pending",
                round_id=round_id,
                phase="-",
                validator_hotkey="-",
                reason=f"fn={fn_name} attempt={attempt + 1}/{max_attempts} err={reason} sleep_ms={sleep_ms:.0f} exhausted=False",
            )
            sleep_sec = sleep_ms / 1000.0
            notify_http_retry_sleep(sleep_sec)
            time.sleep(sleep_sec)
            total_slept_ms += sleep_ms

    log_vv_error(
        "platform_http_retry_exhausted",
        round_id=round_id,
        phase="-",
        validator_hotkey="-",
        reason=f"fn={fn_name} attempts={max_attempts} last={last_reason} exhausted=True",
    )
    return last_val
