# Vividverse Architecture: Bittensor Alignment & Platform Integration

This document verifies alignment with [Bittensor documentation](https://docs.bittensor.com/) and describes tight integration with vividverse-platform.

**Principle:** The subnet/validator/miner/mechanism is the real system. The platform is the interface. See `mechanism/docs/MECHANISM_PLATFORM_BOUNDARY.md` and `docs/MECHANISM_ALIGNMENT_AUDIT.md`.

## Bittensor Architecture (per docs.bittensor.com)

### Core Components

| Component | Role | Bittensor Docs |
|-----------|------|----------------|
| **Axon** | Server — miners deploy Axon to receive validator requests | [Axon API](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/axon/index.html) |
| **Dendrite** | Client — validators use Dendrite to query miners | [Dendrite API](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/dendrite/index.html) |
| **Synapse** | Data schema — Pydantic-based objects exchanged between neurons | [Synapse API](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/synapse/index.html) |
| **Metagraph** | Subnet state — UIDs, hotkeys, axons, validator_permit, emissions | [Metagraph](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/metagraph/index.html) |
| **Subtensor** | Blockchain gateway — set_weights, registration | [Subtensor](https://docs.bittensor.com/python-api/html/autoapi/bittensor/core/subtensor/index.html) |

### Validator Role (per docs.bittensor.com/validators)

1. **Gateway** — Users/apps query subnet through validator hotkeys
2. **Validation** — Score miner responses and submit weights via `set_weights`

Requirements: registered hotkey, UID, stake weight ≥ 1000, top 64 by emissions.

### Miner Role (per docs.bittensor.com/miners)

1. **Produce commodity** — Generate work per subnet incentive mechanism
2. **Publish Axon** — Register IP:PORT on chain via `axon.serve(netuid, subtensor).start()`
3. **Respond to queries** — Handle Synapse requests from validators

### Subnet Flow

```
Validator (Dendrite)  ──Synapse──►  Miner (Axon)
Validator (set_weights) ──────────►  Blockchain (Subtensor)
Miner (serve)         ──────────►  Blockchain (advertise endpoint)
```

---

## Vividverse Mechanism Alignment

### Miner (`neurons/miner.py`)

| Bittensor Pattern | Implementation |
|-------------------|----------------|
| Axon as server | `bt.axon(wallet, config)` ✓ |
| Attach forward_fn, blacklist_fn | `axon.attach(forward_fn, blacklist_fn, synapse_type=RoundStateQuery)` ✓ |
| Blacklist non-validators | `_blacklist_round_state` checks `validator_permit` ✓ |
| Register on chain | `axon.serve(netuid, subtensor).start()` when available ✓ |
| Synapse types | `RoundStateQuery`, `SubmissionSynapse` (extend `bt.Synapse`) ✓ |

### Validator (`neurons/validator.py`)

| Bittensor Pattern | Implementation |
|-------------------|----------------|
| Dendrite to query miners | `dendrite(axons, synapse, timeout)` ✓ |
| Metagraph for axons | `metagraph.axons`, `metagraph.hotkeys`, `metagraph.validator_permit` ✓ |
| set_weights on chain | `subtensor.set_weights(wallet, netuid, uids, weights)` ✓ |
| Miner UIDs (non-validator) | `_get_miner_uids` filters by `not validator_permit` ✓ |

### Protocol (`vividverse/protocol.py`)

| Bittensor Pattern | Implementation |
|-------------------|----------------|
| Synapse extends bt.Synapse | `RoundStateQuery(bt.Synapse)`, `SubmissionSynapse(bt.Synapse)` ✓ |
| Pydantic fields | All fields typed; Optional for response fields ✓ |
| Request vs response | Validator sets request; miner fills response ✓ |

---

## Platform Integration

### Roles Mapping

| Platform Role | Subnet Role | Integration |
|---------------|-------------|-------------|
| Miner | Miner | Submits via Platform UI; miner neuron fetches metadata from Platform API |
| Critic | Validator (scoring) | Critics score on Platform → ValidatorScore → subnet scores endpoint |
| Validator | Validator | Platform validators run mechanism validator with PLATFORM_API_URL |
| Admin | — | Platform admin; no direct subnet role |

### Data Flow

```
Platform (Next.js)                    Mechanism (Python)
─────────────────                    ─────────────────
GET /api/miner/metadata     ◄──────  Miner fetches submission metadata
GET /api/rounds/current     ◄──────  Miner cold start, Validator round state
GET /api/subnet/rounds/[id]/state   ◄──────  Validator fetches phase, deadlines
GET /api/subnet/rounds/[id]/scores  ◄──────  Validator fetches critic scores

Critics score via /api/validator/score  →  ValidatorScore  →  rawScore on Submission
                                                              ↓
Validator finalisation  →  fetch scores  →  compute_weights  →  set_weights
```

### Phase & Cadence

- **Platform phases**: `prompt_voting` → `submission` → `evaluation` → `finalised`
- **Subnet phases** (mirrored): `submission` → `evaluation` → `finalised`
- **Cadence**: Platform drives deadlines; validator uses `submission_deadline_unix`, `evaluation_deadline_unix` from Platform

### Submission Types

| Platform Field | Protocol Field | Notes |
|----------------|----------------|-------|
| submissionHash | submission_hash | ✓ |
| submissionUrl | submission_url | ✓ |
| durationSeconds | duration_seconds | ✓ |
| hasAudio | has_audio | ✓ |
| narrativeProgression | narrative_progression | ✓ (Round 2+) |
| minerHotkey | miner_hotkey | ✓ |

---

## Environment & Usage

### Miner (Platform mode)

```bash
PLATFORM_API_URL=http://localhost:3000 python neurons/miner.py --netuid 1 --subtensor.network local
```

### Validator (Platform mode)

```bash
PLATFORM_API_URL=http://localhost:3000 python neurons/validator.py --netuid 1 --subtensor.network local
```

### Standalone (no Platform)

Omit `PLATFORM_API_URL`; validator uses local RoundStateManager and SQLite scores.

---

## Mechanism–Platform Boundary

See **MECHANISM_PLATFORM_BOUNDARY.md** for what the validator/miner actually enforce vs consume. In Platform mode, lifecycle (phases, deadlines, rounds) is owned by Platform; mechanism consumes via API.
