# Vividverse Testnet — Judge Setup

**Platform:** https://staging.vividverse.ai
**Network:** Bittensor testnet · Netuid 210

---

> A validator must have at least 1 critic tied to their account.
> The validator hotkey run in the terminal is matched to the platform account.

---

## Running the Validator

**Requires Docker only** — no Python environment, no btcli, no wallet setup needed.

```bash
git clone https://github.com/VividVerseAI/Hackquest-Round-2-Vividverse.git
cd Hackquest-Round-2-Vividverse
docker build -t vividverse-validator .
docker run vividverse-validator
```

This runs **Validator 1** (miner/hotkey3) by default. To run **Validator 2** instead:

```bash
docker run -e WALLET_HOTKEY=hotkey1 vividverse-validator
```

The Docker image handles everything: installs all dependencies, pre-bakes the testnet wallets, and connects to the platform at https://staging.vividverse.ai automatically.

---

## Signing In to the Platform

The platform uses hotkey-based authentication. Since these are Bittensor wallets (not Polkadot.js/Talisman), use `scripts/sign_challenge.py` to sign in — or import the mnemonic phrases from `mechanism/scripts/testnet_wallets.txt` into Talisman/Polkadot.js directly (these wallets support Substrate).

### Step 1 — Go to the platform and enter your hotkey

1. Go to https://staging.vividverse.ai and click **Sign In**
2. Click **"Already linked a hotkey? Sign in by verifying it"**
3. Enter the validator hotkey SS58 address (see credentials below) and click **Continue**

### Step 2 — Copy the challenge message

The platform displays a challenge message. Click **"Copy message"** or copy the full text manually. It looks like:

```
Vividverse hot key verification
Domain: staging.vividverse.ai
Hotkey: <your hotkey SS58>
Netuid: 210
Nonce: <random>
Issued: <timestamp>
Expires: <timestamp>
```

### Step 3 — Sign it

**Option A — sign_challenge.py** (from the `mechanism/` directory after `pip install -r requirements.txt`):

```bash
python3 scripts/sign_challenge.py -m "PASTE_FULL_MESSAGE_HERE" \
  --wallet.name miner \
  --wallet.hotkey hotkey3
```

Replace `hotkey3` with the hotkey label for the validator you are signing in as. The script prints a `0x...` hex signature.

**Option B — Talisman / Polkadot.js:** Import the mnemonic from `mechanism/scripts/testnet_wallets.txt` for the relevant hotkey, then sign via the browser extension.

### Step 4 — Paste the signature

Back on the platform, scroll past "Sign with Polkadot.js / Talisman" to the **"or paste signature"** field. Paste the `0x...` hex and click **Submit signature**.

---

## Owner Validator *(running in the cloud — no command needed)*

```
wallet.name : testnetwallet
wallet.hotkey: hotkey1
hotkey SS58  : 5G4f6JH8prntQG8WQRygJ2azF8YnkeDzPc2SvZm71KwK3kkg
```

Sign in command:
```bash
python3 scripts/sign_challenge.py -m "PASTE_MESSAGE" \
  --wallet.name testnetwallet \
  --wallet.hotkey hotkey1
```

| Critic | Email | Password |
|--------|-------|----------|
| Critic 5 | critic-5fc6520fa8ab3e1c@vividverse.invite | 15nLV-QihYE7QXW9 |
| Critic 6 | critic-5394105bb268ef6a@vividverse.invite | ZS5J_lYmGP4Hhor- |

---

## Validator 1

```bash
docker run vividverse-validator
```

```
wallet.name : miner
wallet.hotkey: hotkey3
hotkey SS58  : 5C8TKtHpZx7TeP4jUDqBW63JmoAwTD7fPgaa91foUtQoYeP1
coldkey SS58 : 5FNBxB84BGdf5yVh5y2tYsgzwQLLE26evNRMpFfyCnSALGms
```

Sign in command:
```bash
python3 scripts/sign_challenge.py -m "PASTE_MESSAGE" \
  --wallet.name miner \
  --wallet.hotkey hotkey3
```

| Critic | Email | Password |
|--------|-------|----------|
| Critic 1 | critic-c5f43a8132d9e55e@vividverse.invite | lxtkSKmmoUUrRs1m |
| Critic 2 | critic-642387c4b8f0ce18@vividverse.invite | uLd0A4pnMONdRhpT |

---

## Validator 2

```bash
docker run -e WALLET_HOTKEY=hotkey1 vividverse-validator
```

```
wallet.name : miner
wallet.hotkey: hotkey1
hotkey SS58  : 5Fk92bfzoV5fYrEY4AVycdeg3mUNDE6h88W1WF5gjbZWveUa
coldkey SS58 : 5FNBxB84BGdf5yVh5y2tYsgzwQLLE26evNRMpFfyCnSALGms
```

Sign in command:
```bash
python3 scripts/sign_challenge.py -m "PASTE_MESSAGE" \
  --wallet.name miner \
  --wallet.hotkey hotkey1
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
