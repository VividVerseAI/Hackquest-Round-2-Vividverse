# Vividverse

**Proof of Cinematic Intelligence — Bittensor Subnet**

Vividverse is a Bittensor subnet where AI filmmakers (miners) compete each round to produce the best next scene of a collaboratively evolving film. Validators score submissions against a narrative rubric via the platform's critic scoring system. The strongest scene becomes canon and sets the baseline for the next round. Emissions reward the winner (70%) and proportionally reward all submissions above a quality threshold (30%).

Miners submit directly via the Vividverse platform — no local axon required. The platform handles submission ingestion, watermarking, AI vision screening, critic scoring, and canonical chain management. The validator mechanism's role is to read finalised scores from the platform and call `set_weights()` on-chain.

---

## Repository Files

### `BUILD.md`
Full implementation brief. Contains protocol definitions, validator neuron, round state machine, incentive mechanism, submission validation, canonical chain, unit tests, and setup script.

### `SUBNET_DESIGN.md`
Conceptual design reference. Covers incentive mechanism design, scoring rubric, emission logic, adversarial protections, and system architecture.

---

## Judging Commands

```bash
# Verify all logic offline — no chain needed
pytest tests/ -v
```

---

## What This Subnet Proves

| Criterion | How Demonstrated |
|---|---|
| Functional subnet logic | Round state machine transitions correctly; submission validation enforced |
| Working validator evaluation flow | Validator reads platform scores, calls `set_weights()` |
| Incentive mechanisms behave as intended | `compute_weights()` implements 70/30 split with 75.00 quality floor; verified by unit tests |

---

## Tech Stack

- **Bittensor SDK v10** — `bt.Synapse`, `bt.dendrite`, `subtensor.set_weights()`
- **Python 3.10–3.11**
- **PyTorch** — weight tensor computation

---

## Testnet Setup

See **[docs/TESTNET_SETUP.md](docs/TESTNET_SETUP.md)** for a step-by-step guide to run on Bittensor testnet: wallets, registration, validator staking, and running the validator node.

```bash
bash scripts/setup.sh env      # Create wallets
bash scripts/setup.sh testnet  # Register on testnet
bash scripts/setup.sh stake    # Stake validator (required for permit)
```

---

## Docs

- Bittensor SDK: https://docs.learnbittensor.org
- Bittensor Docs: https://docs.bittensor.com
- SDK v10 Migration: https://docs.learnbittensor.org/sdk/migration-guide
- Subnet Template: https://github.com/opentensor/bittensor-subnet-template
