FROM python:3.10-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Make Subnet/ importable as "vividverse"
RUN cp -r Subnet vividverse

# Install CPU-only torch (keeps image size down), then remaining deps
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r mechanism/requirements.txt

ENV PYTHONPATH=/app
ENV VALIDATOR_PLATFORM_API_URL=https://staging.vividverse.ai

WORKDIR /app/mechanism

# Wallet credentials are provided at runtime via the entrypoint env vars:
#   BT_HOTKEY_JSON_BASE64   — base64(hotkey.json)
#   BT_COLDKEYPUB_BASE64    — base64(coldkeypub.txt)
#   BT_WALLET_NAME          — wallet folder name  (default: miner)
#   BT_HOTKEY_NAME          — hotkey file name    (default: hotkey3)
#
# See mechanism/scripts/testnet_wallets.txt for mnemonic phrases to restore wallets,
# and Run & Setup.md for the sign-in guide.
#
# Example:
#   docker run \
#     -e BT_HOTKEY_JSON_BASE64=<base64> \
#     -e BT_COLDKEYPUB_BASE64=<base64> \
#     -e BT_WALLET_NAME=miner \
#     -e BT_HOTKEY_NAME=hotkey3 \
#     vividverse-validator

ENTRYPOINT ["bash", "scripts/validator-entrypoint.sh"]

