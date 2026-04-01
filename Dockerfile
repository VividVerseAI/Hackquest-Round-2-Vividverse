FROM python:3.10-slim

# System deps
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Make Subnet/ importable as "vividverse"
RUN cp -r Subnet vividverse

# Install CPU-only torch (keeps image size down), then remaining deps
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r mechanism/requirements.txt

# Pre-restore testnet wallets from mnemonics so the judge does not need wallet files.
# These are testnet-only credentials — already public in Run & Setup.md.

# miner coldkeypub (shared by hotkey1 and hotkey3)
RUN btcli wallet regen_coldkeypub \
      --wallet.name miner \
      --ss58_address 5FNBxB84BGdf5yVh5y2tYsgzwQLLE26evNRMpFfyCnSALGms \
      --no_prompt

# Validator 1 — miner/hotkey3 (default)
RUN btcli wallet regen_hotkey \
      --wallet.name miner \
      --wallet.hotkey hotkey3 \
      --mnemonic "naive bread mansion swing helmet zebra wife test diagram obscure grass column" \
      --no_password \
      --no_prompt

# Validator 2 — miner/hotkey1
RUN btcli wallet regen_hotkey \
      --wallet.name miner \
      --wallet.hotkey hotkey1 \
      --mnemonic "amateur leaf rely lamp unfair child marine budget merit square floor nest" \
      --no_password \
      --no_prompt

# Python path so "import vividverse" resolves to /app/vividverse/
ENV PYTHONPATH=/app
ENV VALIDATOR_PLATFORM_API_URL=https://staging.vividverse.ai

WORKDIR /app/mechanism

# Default: Validator 1. Switch to Validator 2 with: -e WALLET_HOTKEY=hotkey1
CMD python3 neurons/validator.py \
      --netuid 210 \
      --subtensor.network test \
      --wallet.name miner \
      --wallet.hotkey ${WALLET_HOTKEY:-hotkey3} \
      --logging.debug

