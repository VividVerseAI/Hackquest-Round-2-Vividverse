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

# Restore registered validator wallet from mnemonics (public in testnet_wallets.txt)
RUN btcli wallet regen_coldkeypub \
      --wallet-name validator \
      --wallet-path /root/.bittensor/wallets \
      --ss58-address 5D7tp3Mabe5ALxtDZNLzWdLdCGrW4moGwE1xyosjBgBNCJit \
      --overwrite && \
    btcli wallet regen_hotkey \
      --wallet-name validator --wallet-path /root/.bittensor/wallets --hotkey hotkey1 \
      --mnemonic "quote time ribbon sample figure deal pact exchange east delay clever dinner" \
      --no-use-password --overwrite && \
    btcli wallet regen_hotkey \
      --wallet-name validator --wallet-path /root/.bittensor/wallets --hotkey hotkey2 \
      --mnemonic "connect typical symptom odor cotton company any street heavy please mean winter" \
      --no-use-password --overwrite && \
    btcli wallet regen_hotkey \
      --wallet-name validator --wallet-path /root/.bittensor/wallets --hotkey hotkey3 \
      --mnemonic "rib window silent lock betray cancel swear sea process chef learn suit" \
      --no-use-password --overwrite

ENV PYTHONPATH=/app
ENV VALIDATOR_PLATFORM_API_URL=https://staging.vividverse.ai

WORKDIR /app/mechanism

# Default: Validator 1 (validator/hotkey1). Switch with: -e WALLET_HOTKEY=hotkey2 or hotkey3
CMD python3 neurons/validator.py \
      --netuid 210 \
      --subtensor.network test \
      --wallet.name validator \
      --wallet.hotkey ${WALLET_HOTKEY:-hotkey1} \
      --logging.debug

