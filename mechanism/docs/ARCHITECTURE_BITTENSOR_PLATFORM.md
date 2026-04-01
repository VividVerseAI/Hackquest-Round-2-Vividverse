# Vividverse Architecture: Bittensor Alignment & Platform Integration

This document describes how the Vividverse subnet uses Bittensor and where it intentionally
departs from the standard template to support a platform-native miner model.

---

## How Vividverse Differs From the Standard Bittensor Template

In the standard Bittensor model, miners run local axon servers, validators query them via
Dendrite/Synapse, and score the responses directly. Vividverse uses a different model:

**Miners are platform-native.** There is no `miner.py`. Miners register their hotkey on
chain, then submit AI-generated video scenes through the Vividverse platform UI. The platform
handles ingestion, watermarking, AI vision screening, and stores submissions against the
miner hotkey. There is no local axon, no IP endpoint advertised on chain, and no
Dendrite/Synapse query path.

**Scoring is done by critics, not directly by validators.** Each validator has a pool of
AI critics. Critics score submissions through the platform. The validator reads those scores
at finalisation time via the platform API and uses them to call `set_weights()`.

This design separates the on-chain accountability layer (validator → `set_weights`) from
the scoring layer (critics → platform), while keeping the metagraph as the source of truth
for miner registration and emissions.

---

## Bittensor Components Used

| Component | Used | How |
|-----------|------|-----|
| **Metagraph** | Yes | Validator reads `hotkeys`, `validator_permit`, `n` for UID mapping and weight tensor size |
| **Subtensor** | Yes | `subtensor.set_weights()` called at round finalisation |
| **Wallet / Hotkey** | Yes | Validator signs lifecycle pushes; hotkey verified by platform against metagraph |
| **Axon** | No | Miners do not run axons — submission is via platform UI |
| **Dendrite** | No | Validator does not query miners directly |
| **Synapse** | No | No direct validator↔miner message passing |

---

## Participant Roles

| Role | On Chain | Platform |
|------|----------|----------|
| **Miner** | Registered hotkey + UID; receives emissions | Submits scenes via platform UI; no local neuron required |
| **Validator** | Registered hotkey + UID; `validator_permit`; calls `set_weights()` | Runs `neurons/validator.py`; pushes lifecycle state; reads critic scores |
| **Critic** | No chain presence | Scores submissions on platform; tied to a validator account |

---

## Data Flow

```
Miner
  └── Registers hotkey on chain (btcli subnet register)
  └── Submits scene via Platform UI  ──►  Platform stores submission + minerHotkey

Critics (per validator pool)
  └── Score submissions via Platform  ──►  Platform stores rawScore per submission

Validator (neurons/validator.py)
  └── Every ~10s:
        GET /api/rounds/current            — current round ID
        GET /api/subnet/rounds/{id}/state  — phase, deadlines
        POST /api/validator/lifecycle      — push heartbeat / phase transition
  └── At finalisation:
        GET /api/subnet/rounds/{id}/scores — critic scores for this validator's pool
        compute_weights(scores)            — 70/30 incentive split
        subtensor.set_weights(...)         — write weights on chain
```

---

## Validator Lifecycle

The validator drives all round state. The platform stores it. Key pushes:

| Push | When | Effect on Platform |
|------|------|--------------------|
| `prompt_voting` heartbeat | Every step while no active round | Registers validator presence |
| `prompt_voting` + deadline | Enough miners have voted | Arms the voting window |
| `submission` | Prompt voting complete | Platform opens submission window |
| `evaluation` | Submission deadline passed | Platform opens evaluation window |
| `finalised` + scores | Critic quorum + tempo boundary met | Platform marks round finalised |

---

## Validator Authentication with Platform

The validator authenticates lifecycle pushes in one of two ways:

- **Metagraph verification (default)** — Platform checks the validator hotkey against the live metagraph via HTTP bridge. Slower but requires no shared secret.
- **Shared secret (optional)** — Set `VALIDATOR_INGEST_SECRET` (matching `PLATFORM_VALIDATOR_INGEST_SECRET` on the platform) for a faster path that skips the metagraph call.

---

## Incentive Mechanism

Scores from the validator's critic pool determine weights. All logic is in `vividverse/contracts/incentive.py`:

```
qualifying = submissions where critic_score >= QUALITY_THRESHOLD (75.0)
winner     = highest scoring qualifying submission (lowest UID on tie)
weights:
  winner              → WINNER_SHARE      (0.70)
  other qualifying    → PROPORTIONAL_SHARE (0.30) split pro-rata by score
  below threshold     → 0
```

Parameters are set in `vividverse/contracts/subnet_settings.json` and loaded at import time. The subnet owner controls them by shipping that file.

---

## Phase & Cadence

```
prompt_voting  →  submission  →  evaluation  →  finalised
```

- **prompt_voting**: Miners vote on the next scene prompt. Validator arms a deadline once enough miners vote, then selects the winning prompt.
- **submission**: Miners submit scenes via the platform UI. Deadline set by validator at round creation.
- **evaluation**: Critics score submissions. Validator waits for critic quorum before finalising.
- **finalised**: Validator calls `set_weights()` on chain. Winning scene becomes the canonical next entry in the film.

Timing constants (`submissionWindowSec`, `evaluationWindowSec`, etc.) live in `vividverse/contracts/subnet_settings.json`.

---

## References

- [Bittensor Validators](https://docs.bittensor.com/validators)
- [Subtensor set_weights](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/subtensor/index.html)
- [Metagraph](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/metagraph/index.html)
- [Yuma Consensus](https://docs.bittensor.com/yuma-consensus)

