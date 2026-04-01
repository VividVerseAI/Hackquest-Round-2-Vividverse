FROM python:3.10-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Make Subnet/ importable as "vividverse"
RUN cp -r Subnet vividverse

# Install CPU-only torch (keeps image size down), then remaining deps
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r mechanism/requirements.txt

# Install btcli for wallet restoration
RUN pip install --no-cache-dir bittensor-cli

# Restore testnet wallets from mnemonics (already public in testnet_wallets.txt)
RUN btcli wallet regen_coldkeypub \
      --wallet.name miner \
      --ss58_address 5FNBxB84BGdf5yVh5y2tYsgzwQLLE26evNRMpFfyCnSALGms \
      --no_prompt && \
    btcli wallet regen_hotkey \
      --wallet.name miner --wallet.hotkey hotkey3 \
      --mnemonic "naive bread mansion swing helmet zebra wife test diagram obscure grass column" \
      --no_password --no_prompt && \
    btcli wallet regen_hotkey \
      --wallet.name miner --wallet.hotkey hotkey1 \
      --mnemonic "amateur leaf rely lamp unfair child marine budget merit square floor nest" \
      --no_password --no_prompt

ENV PYTHONPATH=/app
ENV VALIDATOR_PLATFORM_API_URL=https://staging.vividverse.ai

WORKDIR /app/mechanism

# Default: Validator 1 (miner/hotkey3). Switch with: -e WALLET_HOTKEY=hotkey1
CMD python3 neurons/validator.py \
      --netuid 210 \
      --subtensor.network test \
      --wallet.name miner \
      --wallet.hotkey ${WALLET_HOTKEY:-hotkey3} \
      --logging.debug

