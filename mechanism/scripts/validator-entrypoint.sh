#!/bin/sh
# Validator Docker entrypoint.
# Decodes Bittensor wallet credentials from env vars, then runs the validator neuron.
#
# Required env vars:
#   BT_HOTKEY_JSON_BASE64  — base64(hotkey.json) — coldkey-encrypted private key
#   BT_COLDKEYPUB_BASE64   — base64(coldkeypub.txt) — SS58 public key
#
# Optional:
#   BT_WALLET_NAME         — wallet folder name  (default: default)
#   BT_HOTKEY_NAME         — hotkey file name    (default: default)
#   BT_COLDKEY_JSON_BASE64 — base64(coldkey) — encrypted private coldkey; required for TAO transfers
#   BT_COLDKEY_PASSWORD    — password to decrypt coldkey at runtime
#
# All other validator flags (--netuid, --subtensor.network, etc.) are passed
# through as CMD arguments or via VALIDATOR_ARGS env var.
set -e

WALLET_NAME="${BT_WALLET_NAME:-default}"
HOTKEY_NAME="${BT_HOTKEY_NAME:-default}"

WALLET_DIR="$HOME/.bittensor/wallets/${WALLET_NAME}"
HOTKEY_DIR="${WALLET_DIR}/hotkeys"

mkdir -p "${HOTKEY_DIR}"

# ── Decode hotkey ──────────────────────────────────────────────────────────────
if [ -z "${BT_HOTKEY_JSON_BASE64}" ]; then
  echo "[entrypoint] ERROR: BT_HOTKEY_JSON_BASE64 is not set." >&2
  echo "[entrypoint] Set this to base64(hotkey.json) in your Railway/Docker env." >&2
  exit 1
fi
echo "${BT_HOTKEY_JSON_BASE64}" | base64 -d > "${HOTKEY_DIR}/${HOTKEY_NAME}"
echo "[entrypoint] Hotkey written to ${HOTKEY_DIR}/${HOTKEY_NAME}"

# ── Decode coldkeypub ──────────────────────────────────────────────────────────
if [ -z "${BT_COLDKEYPUB_BASE64}" ]; then
  echo "[entrypoint] ERROR: BT_COLDKEYPUB_BASE64 is not set." >&2
  echo "[entrypoint] Set this to base64(coldkeypub.txt) in your Railway/Docker env." >&2
  exit 1
fi
echo "${BT_COLDKEYPUB_BASE64}" | base64 -d > "${WALLET_DIR}/coldkeypub.txt"
echo "[entrypoint] Coldkeypub written to ${WALLET_DIR}/coldkeypub.txt"

# ── Decode coldkey (optional — required for critic TAO transfers) ──────────────
if [ -n "${BT_COLDKEY_JSON_BASE64}" ]; then
  echo "${BT_COLDKEY_JSON_BASE64}" | base64 -d > "${WALLET_DIR}/coldkey"
  chmod 600 "${WALLET_DIR}/coldkey"
  echo "[entrypoint] Coldkey written to ${WALLET_DIR}/coldkey"
else
  echo "[entrypoint] BT_COLDKEY_JSON_BASE64 not set — critic TAO transfers disabled"
fi

# ── Launch validator ───────────────────────────────────────────────────────────
echo "[entrypoint] Starting vividverse validator..."
echo "[entrypoint] Wallet: ${WALLET_NAME} / Hotkey: ${HOTKEY_NAME}"

# Pass VALIDATOR_ARGS env var + any CMD args to the neuron
NETUID="${VALIDATOR_NETUID:-210}"
NETWORK="${VALIDATOR_SUBTENSOR_NETWORK:-test}"

echo "[entrypoint] netuid=${NETUID} network=${NETWORK} VALIDATOR_ARGS=${VALIDATOR_ARGS:-}"

exec python neurons/validator.py \
  --wallet.name "${WALLET_NAME}" \
  --wallet.hotkey "${HOTKEY_NAME}" \
  --netuid "${NETUID}" \
  --subtensor.network "${NETWORK}" \
  ${VALIDATOR_ARGS:-} \
  "$@"
