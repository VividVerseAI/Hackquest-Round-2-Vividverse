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

# Run the validator — set WALLET_HOTKEY to switch between validators (default: hotkey3)
# Wallets are read from ~/.bittensor/wallets/ — mount your local wallet directory:
#   docker run -v ~/.bittensor:/root/.bittensor vividverse-validator
CMD python3 neurons/validator.py \
      --netuid 210 \
      --subtensor.network test \
      --wallet.name miner \
      --wallet.hotkey ${WALLET_HOTKEY:-hotkey3} \
      --logging.debug

