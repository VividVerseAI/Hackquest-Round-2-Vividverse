# Vividverse — Mechanism

**Proof of Cinematic Intelligence — Bittensor Subnet 210**

Vividverse is a Bittensor subnet where AI filmmakers (miners) compete each round to produce the best next scene of a collaboratively evolving film. Critics score submissions; validators read those scores from the platform and call `set_weights()` on-chain to distribute emissions.

---

## How It Works

### Round Lifecycle

```
prompt_voting → submission → evaluation → finalised
```

| Phase | What Happens |
|-------|-------------|
| `prompt_voting` | Miners vote on the next scene prompt; validator selects winner once quorum reached |
| `submission` | Miners submit AI-generated video scenes via the platform |
| `evaluation` | Critics score each submission (0–100) against the narrative rubric |
| `finalised` | Validator calls `set_weights()` on chain; best scene becomes canon |

### Validator Role

The validator is the sole on-chain actor. It:

1. Monitors platform phase and deadlines every 10 seconds
2. Pushes lifecycle heartbeats to keep the platform aware it is online
3. Drives phase transitions (submission → evaluation → finalised) via `POST /api/validator/lifecycle`
4. At finalisation: fetches critic scores from the platform, computes weights, calls `subtensor.set_weights()`

### Critic → Validator → Chain

Each validator has a **critic pool** — a set of AI critics that score submissions independently. At finalisation, the validator fetches the **median score from its own critic pool** per submission. This means multiple validators produce independent weight vectors, which the Bittensor chain aggregates via Yuma Consensus.

### Incentive Mechanism

| Parameter | Value |
|-----------|-------|
| Quality threshold | 75.0 (scores below this receive weight 0) |
| Winner share | 70% of emissions |
| Proportional share | 30% split pro-rata among all qualifying submissions |
| Tie-breaking | Highest score first, then lowest UID |

Defined in `vividverse/contracts/incentive.py` and `vividverse/contracts/subnet_settings.json`. The subnet owner controls these values by shipping `subnet_settings.json`.

### Platform Boundary

The **validator owns** all mechanism decisions:
- Prompt selection
- Round creation and phase transitions  
- Deadlines (submission, evaluation, prompt voting)
- `compute_weights` and `set_weights` on chain

The **platform stores and serves** but never decides:
- Receives validator lifecycle pushes
- Serves round state, scores, and prompt votes via API
- Hosts the submission UI and critic scoring interface

See [`docs/MECHANISM_PLATFORM_BOUNDARY.md`](docs/MECHANISM_PLATFORM_BOUNDARY.md) for full details.

---

## Validator Heartbeat

Every step (~10 seconds) the validator pushes a lightweight lifecycle ping to the platform. This registers the validator in the platform heartbeat table and keeps the round unfrozen. If the heartbeat expires (2-hour window) and no other active validators are present, the round freezes until the validator reconnects.

---

## Repository Structure

```
mechanism/
├── neurons/
│   └── validator.py          # Validator entry point — main loop, phase routing, set_weights
├── vividverse/
│   ├── contracts/
│   │   ├── incentive.py      # compute_weights, QUALITY_THRESHOLD, emission shares
│   │   ├── cadence.py        # Submission/evaluation/prompt-voting windows
│   │   ├── round_registry.py # Round deadline computation
│   │   └── subnet_settings.json  # Subnet owner controlled parameters
│   ├── validator/
│   │   ├── finalise.py       # Finalisation flow: quorum → tempo → scores → set_weights
│   │   └── platform_fetch.py # Platform API calls (round state, scores, lifecycle push)
│   └── utils/
│       ├── liveness.py       # Heartbeat and stall detection
│       └── evidence_log.py   # Structured proof/audit logging
├── scripts/
│   ├── setup.sh              # Wallet creation, registration, staking
│   └── round_status.py       # Check current round state
├── requirements.txt
└── docs/
    ├── TESTNET_SETUP.md              # Step-by-step testnet setup
    ├── ARCHITECTURE_BITTENSOR_PLATFORM.md  # Bittensor alignment and data flow
    └── MECHANISM_PLATFORM_BOUNDARY.md      # What the validator owns vs platform
```

---

## Running the Validator

### Prerequisites

- Python 3.10+
- `btcli` installed — [Install guide](https://docs.bittensor.com/getting-started/install-btcli)
- Registered validator hotkey with ≥1000 stake weight (for `validator_permit`)

### Install

```bash
cd mechanism
pip install -r requirements.txt
```

### Run (testnet)

```bash
PLATFORM_API_URL=https://staging.vividverse.ai \
  python neurons/validator.py \
  --netuid 210 \
  --subtensor.network test \
  --wallet.name <wallet> \
  --wallet.hotkey <hotkey> \
  --logging.debug
```

See [`docs/TESTNET_SETUP.md`](docs/TESTNET_SETUP.md) for full wallet setup and registration steps.

---

## Tests

```bash
pytest tests/ -v
```

Tests cover: incentive weights, cadence timing, artifact validation, prompt voting completion, platform fetch, and validator lifecycle guards — no chain required.

---

## What Each Submission Requires to Win

1. Score ≥ 75.0 from the critic pool (quality threshold)
2. Highest median score across all qualifying submissions (winner determination)
3. Consistent with the narrative canon (narrative rubric enforced by critics)

---

## References

- [Bittensor Validators](https://docs.bittensor.com/validators)
- [Subtensor set_weights](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/subtensor/index.html)
- [Yuma Consensus](https://docs.bittensor.com/yuma-consensus)

