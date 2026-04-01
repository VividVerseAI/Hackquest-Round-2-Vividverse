# Vividverse Bittensor Testnet Setup Guide

This guide walks you through setting up the Vividverse validator for **Bittensor testnet**.

Miners submit directly via the Vividverse platform — no local node required. This guide covers the validator only.

## Prerequisites

- **macOS or Linux**
- **Python 3.10+**
- **btcli** installed ([Install BTCLI](https://docs.bittensor.com/getting-started/install-btcli))
- **Test TAO** — Request from [Bittensor Discord](https://discord.com/channels/799672011265015819/1107738550373454028)

## Overview

| Step | Purpose |
|------|---------|
| 1. Install dependencies | Python packages, btcli |
| 2. Create validator wallet | Coldkey + hotkey |
| 3. Fund wallet | Get test TAO from faucet |
| 4. Create subnet (optional) | If you own the subnet |
| 5. Register validator | On subnet |
| 6. Stake validator | Required for validator permit (≥1000 stake weight) |
| 7. Run validator | Start validator node |

---

## 1. Install Dependencies

```bash
cd project/mechanism
pip install -r requirements.txt
```

Verify btcli:

```bash
btcli --version
```

---

## 2. Create Wallet

```bash
btcli wallet create --wallet.name validator --no_password
btcli wallet new_hotkey --wallet.name validator --wallet.hotkey default --no_password
```

Or use the setup script:

```bash
bash scripts/setup.sh env
```

---

## 3. Fund Wallet

1. Join [Bittensor Discord](https://discord.com/channels/799672011265015819/1107738550373454028)
2. Request test TAO in the testnet faucet channel
3. Provide your **validator coldkey address**

```bash
btcli wallet overview --wallet.name validator
```

---

## 4. Create Subnet (Optional)

Only if you are **creating a new subnet**:

```bash
btcli subnet burn-cost --network test
btcli subnet create --network test
btcli subnet start --netuid <YOUR_NETUID> --network test
```

---

## 5. Register Validator

```bash
btcli subnet register \
  --netuid <NETUID> \
  --wallet.name validator \
  --wallet.hotkey default \
  --network test
```

Or use the setup script (after setting `NETUID_TESTNET`):

```bash
bash scripts/setup.sh testnet
```

---

## 6. Stake Validator (Required)

Validators **must have stake weight ≥ 1000** to obtain a validator permit and set weights.

```bash
btcli stake add \
  --netuid <NETUID> \
  --wallet.name validator \
  --wallet.hotkey default \
  --amount 100 \
  --network test
```

Verify permit:

```bash
btcli wallet overview --netuid <NETUID> --wallet.name validator
```

**VPERMIT** should show `*` when you have a validator permit.

---

## 7. Run Validator

```bash
python neurons/validator.py \
  --netuid <NETUID> \
  --subtensor.network test \
  --wallet.name validator \
  --wallet.hotkey default
```

**With Platform integration** (validator reads round state and critic scores from the platform):

```bash
PLATFORM_API_URL=https://your-platform-url.com python neurons/validator.py \
  --netuid <NETUID> \
  --subtensor.network test \
  --wallet.name validator \
  --wallet.hotkey default
```

**Optional — shared secret (faster path):** If the platform operator has set `VALIDATOR_INGEST_SECRET`:

```bash
PLATFORM_API_URL=https://your-platform-url.com \
VALIDATOR_INGEST_SECRET=<secret-from-platform-operator> \
python neurons/validator.py \
  --netuid <NETUID> \
  --subtensor.network test \
  --wallet.name validator \
  --wallet.hotkey default
```

---

## Verification

```bash
btcli wallet overview --netuid <NETUID> --wallet.name validator
btcli subnets metagraph --netuid <NETUID> --network test
```

---

## Network Reference

| Network | Flag | Use case |
|---------|------|----------|
| Testnet | `--subtensor.network test` | Development, testing |
| Mainnet (Finney) | `--subtensor.network finney` | Production |
| Local | `--subtensor.network local` | Local subtensor node |

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `Validator permit: False` | Insufficient stake | Run `btcli stake add` |
| `Connection refused` | Wrong network | Use `--subtensor.network test` for testnet |
| `No scored submissions` | No critics scored yet | Wait for evaluation phase on platform |
| Registration fails | Insufficient TAO | Get test TAO from Discord faucet |

---

## References

- [Bittensor Validators](https://docs.bittensor.com/validators)
- [Create a Subnet](https://docs.bittensor.com/subnets/create-a-subnet)
- [BTCLI Reference](https://docs.bittensor.com/btcli)
