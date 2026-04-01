# Vividverse Testnet — Judge Setup

**Platform:** https://staging.vividverse.ai
**Network:** Bittensor testnet · Netuid 210

---

> A validator must have at least 1 critic tied to their account.
> The validator hotkey run in the terminal is matched to the platform account.

---

## Install

```bash
cd mechanism
pip install -r requirements.txt
pip install -e .
```

`pip install -e .` installs the `vividverse` package (located in `Subnet/`) so the validator can import it. This must be run from the `mechanism/` directory.

---

## Owner Validator *(running in the cloud — no command needed)*

```
wallet.name : testnetwallet
wallet.hotkey: hotkey1
hotkey SS58  : 5G4f6JH8prntQG8WQRygJ2azF8YnkeDzPc2SvZm71KwK3kkg
```

| Critic | Email | Password |
|--------|-------|----------|
| Critic 5 | critic-5fc6520fa8ab3e1c@vividverse.invite | 15nLV-QihYE7QXW9 |
| Critic 6 | critic-5394105bb268ef6a@vividverse.invite | ZS5J_lYmGP4Hhor- |

---

## Validator 1

```bash
VALIDATOR_PLATFORM_API_URL=https://staging.vividverse.ai \
  python3 neurons/validator.py \
  --netuid 210 \
  --subtensor.network test \
  --wallet.name miner \
  --wallet.hotkey hotkey3 \
  --logging.debug
```

```
wallet.name : miner
wallet.hotkey: hotkey3
hotkey SS58  : 5C8TKtHpZx7TeP4jUDqBW63JmoAwTD7fPgaa91foUtQoYeP1
coldkey SS58 : 5FNBxB84BGdf5yVh5y2tYsgzwQLLE26evNRMpFfyCnSALGms
```

| Critic | Email | Password |
|--------|-------|----------|
| Critic 1 | critic-c5f43a8132d9e55e@vividverse.invite | lxtkSKmmoUUrRs1m |
| Critic 2 | critic-642387c4b8f0ce18@vividverse.invite | uLd0A4pnMONdRhpT |

---

## Validator 2

```bash
VALIDATOR_PLATFORM_API_URL=https://staging.vividverse.ai \
  python3 neurons/validator.py \
  --netuid 210 \
  --subtensor.network test \
  --wallet.name miner \
  --wallet.hotkey hotkey1 \
  --logging.debug
```

```
wallet.name : miner
wallet.hotkey: hotkey1
hotkey SS58  : 5Fk92bfzoV5fYrEY4AVycdeg3mUNDE6h88W1WF5gjbZWveUa
coldkey SS58 : 5FNBxB84BGdf5yVh5y2tYsgzwQLLE26evNRMpFfyCnSALGms
```

| Critic | Email | Password |
|--------|-------|----------|
| Critic 3 | critic-06f8e55112db2b71@vividverse.invite | PH5cqGtpVwnvNAoA |
| Critic 4 | critic-bf1b2e1bad95021c@vividverse.invite | o9yVgngxJfOywuYr |

---

## Miners *(confirmed on metagraph — submit via platform UI)*

| Wallet / Hotkey | Hotkey SS58 |
|-----------------|-------------|
| miner / hotkey2 | 5FxrPr1XYJDivpihzPLgCDL6m2ThUMnnsbPrRKBCazcMNB4Z |
| miner / hotkey4 | 5HEqhxMu1KSpjoVU4ZmR2hEm1GYfcDKhwsRt8iWVW7qKYwtd |
| miner / hotkey5 | 5CM4NbW6SqQAXH3motBvnoRo2ApbJtCGfmF8yD2YiAK97sMz |
| miner / hotkey6 | 5EJV4HfTRZcosnBpKsjfhwopdzdzhe19LRhsxZgVHbpU9hwp |
| miner / hotkey7 | 5ChqbcaDqfUAQiiWCd9SHwzKuJSHcqEgba7GKmFsFXqomS7h |
| miner2 / hotkey1 | 5DyVxaG76XRvS2pwgB8DtoBR7176Gi6owucvJgrSdC1Rb2d3 |
| miner2 / hotkey2 | 5EBzeWK9Kgx4jsfPXPbfLaHGcRG8bgPsGPLYPnTQxeAp3mYT |

