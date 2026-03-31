"""
vividverse/validator/critic_transfers.py

Executes on-chain TAO transfers to eligible critics after a round is finalised.

Flow (called after lifecycle push accepted for a finalised round):
  1. GET /api/validator/critics/payout-queue?roundId=X  — fetch CALCULATED rows with wallets
  2. subtensor.transfer() for each entry using the validator wallet (coldkey required)
  3. POST /api/validator/critics/payout-confirm         — report results back to platform

The coldkey must be present on disk (written by entrypoint from BT_COLDKEY_JSON_BASE64)
and unlocked via BT_COLDKEY_PASSWORD before transfers can be signed.

If the coldkey is unavailable or the queue is empty, this is a no-op — finalisation
is not blocked and the platform ledger stays in CALCULATED for manual reconciliation.
"""
from __future__ import annotations

import os
import logging
from typing import Optional, List, Dict, Any

import requests
import bittensor as bt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = int(os.environ.get("PLATFORM_API_TIMEOUT", "60"))


def _normalize_url(url: str) -> str:
    url = (url or "").rstrip("/")
    if url and not url.endswith("/api"):
        url = f"{url}/api"
    return url


def _auth_headers() -> Dict[str, str]:
    secret = os.environ.get("VALIDATOR_INGEST_SECRET") or os.environ.get("PLATFORM_VALIDATOR_INGEST_SECRET")
    if secret:
        return {"x-validator-secret": secret}
    return {}


def _unlock_coldkey(wallet: bt.wallet) -> bool:
    """
    Unlock the wallet coldkey non-interactively using BT_COLDKEY_PASSWORD env var.
    Returns True if unlocked, False if unavailable (no coldkey file or no password).
    """
    password = os.environ.get("BT_COLDKEY_PASSWORD", "")
    coldkey_path = os.path.join(
        os.path.expanduser("~"),
        ".bittensor", "wallets",
        wallet.name,
        "coldkey",
    )
    if not os.path.exists(coldkey_path):
        logger.warning(
            "[critic_transfers] coldkey file not found at %s — "
            "set BT_COLDKEY_JSON_BASE64 in Railway to enable TAO transfers",
            coldkey_path,
        )
        return False

    if not password:
        logger.warning(
            "[critic_transfers] BT_COLDKEY_PASSWORD not set — "
            "cannot unlock coldkey for critic TAO transfers"
        )
        return False

    try:
        wallet.coldkey_file.decrypt(password)
        return True
    except Exception as e:
        logger.error("[critic_transfers] Failed to unlock coldkey: %s", e)
        return False


def _fetch_payout_queue(
    platform_api_url: str,
    round_id: int,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch CALCULATED payout rows ready for transfer."""
    try:
        base = _normalize_url(platform_api_url)
        resp = requests.get(
            f"{base}/validator/critics/payout-queue",
            params={"roundId": round_id},
            headers=_auth_headers(),
            timeout=_DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("queue", [])
        logger.warning(
            "[critic_transfers] payout-queue http_%d round=%d: %s",
            resp.status_code, round_id, resp.text[:200],
        )
        return None
    except Exception as e:
        logger.error("[critic_transfers] payout-queue fetch error round=%d: %s", round_id, e)
        return None


def _confirm_transfers(
    platform_api_url: str,
    transfers: List[Dict[str, Any]],
) -> bool:
    """Report transfer results back to platform."""
    try:
        base = _normalize_url(platform_api_url)
        resp = requests.post(
            f"{base}/validator/critics/payout-confirm",
            json={"transfers": transfers},
            headers={**_auth_headers(), "Content-Type": "application/json"},
            timeout=_DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            return True
        logger.warning(
            "[critic_transfers] payout-confirm http_%d: %s",
            resp.status_code, resp.text[:200],
        )
        return False
    except Exception as e:
        logger.error("[critic_transfers] payout-confirm error: %s", e)
        return False


def execute_critic_transfers(
    subtensor: bt.subtensor,
    wallet: bt.wallet,
    platform_api_url: str,
    round_id: int,
) -> None:
    """
    Execute TAO transfers to eligible critics for a finalised round.
    Silently no-ops if coldkey unavailable, queue empty, or platform unreachable.
    Never raises — critic transfers must not block round progression.
    """
    try:
        if not _unlock_coldkey(wallet):
            return

        queue = _fetch_payout_queue(platform_api_url, round_id)
        if not queue:
            logger.info(
                "[critic_transfers] round=%d: payout queue empty or unavailable — no transfers",
                round_id,
            )
            return

        logger.info(
            "[critic_transfers] round=%d: executing %d critic transfer(s)",
            round_id, len(queue),
        )

        results: List[Dict[str, Any]] = []
        for entry in queue:
            payout_id = entry.get("criticPayoutId")
            dest = entry.get("payoutWalletSs58")
            amount = entry.get("rewardAmount")

            if not payout_id or not dest or not amount:
                logger.warning("[critic_transfers] skipping malformed queue entry: %s", entry)
                continue

            try:
                success = subtensor.transfer(
                    wallet=wallet,
                    dest=dest,
                    amount=bt.Balance.from_tao(float(amount)),
                    wait_for_inclusion=True,
                    wait_for_finalization=False,
                )
                if success:
                    logger.info(
                        "[critic_transfers] round=%d transfer OK: %s TAO → %s",
                        round_id, amount, dest,
                    )
                    results.append({
                        "criticPayoutId": payout_id,
                        "status": "paid",
                    })
                else:
                    logger.warning(
                        "[critic_transfers] round=%d transfer FAILED: %s TAO → %s",
                        round_id, amount, dest,
                    )
                    results.append({
                        "criticPayoutId": payout_id,
                        "status": "failed",
                        "failureReason": "subtensor.transfer returned False",
                    })
            except Exception as transfer_err:
                logger.error(
                    "[critic_transfers] round=%d exception transferring to %s: %s",
                    round_id, dest, transfer_err,
                )
                results.append({
                    "criticPayoutId": payout_id,
                    "status": "failed",
                    "failureReason": str(transfer_err)[:400],
                })

        if results:
            _confirm_transfers(platform_api_url, results)
            paid = sum(1 for r in results if r["status"] == "paid")
            failed = sum(1 for r in results if r["status"] == "failed")
            logger.info(
                "[critic_transfers] round=%d complete: %d paid, %d failed",
                round_id, paid, failed,
            )

    except Exception as e:
        logger.error("[critic_transfers] unexpected error round=%d: %s", round_id, e)
