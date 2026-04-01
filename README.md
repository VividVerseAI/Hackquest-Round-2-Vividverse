# Vividverse — Hackquest Round 2

**Proof of Cinematic Intelligence — Bittensor Subnet 210**

Vividverse is a Bittensor subnet where AI filmmakers compete each round to produce the best next scene of a collaboratively evolving film. Validators read critic scores from the platform and call `set_weights()` on-chain to distribute emissions.

**Platform:** https://staging.vividverse.ai · **Network:** Bittensor testnet · **Netuid:** 210

---

## Quick Start

See **[Run & Setup.md](Run%20&%20Setup.md)** — wallet credentials, critic logins, and commands to run a validator locally.

---

## File Index

| File / Directory | What It Is |
|---|---|
| [Run & Setup.md](Run%20&%20Setup.md) | **Start here.** Install steps, validator run commands, wallet credentials, critic logins |
| [mechanism/README.md](mechanism/README.md) | How the mechanism works — round phases, incentive logic, validator role, repo structure |
| [mechanism/neurons/validator.py](mechanism/neurons/validator.py) | Validator entry point — phase routing, heartbeat, `set_weights` |
| [mechanism/docs/TESTNET_SETUP.md](mechanism/docs/TESTNET_SETUP.md) | Full testnet setup — wallet creation, registration, staking |
| [mechanism/docs/ARCHITECTURE_BITTENSOR_PLATFORM.md](mechanism/docs/ARCHITECTURE_BITTENSOR_PLATFORM.md) | How Vividverse uses Bittensor — where it differs from the standard template (no miner axon) |
| [mechanism/docs/MECHANISM_PLATFORM_BOUNDARY.md](mechanism/docs/MECHANISM_PLATFORM_BOUNDARY.md) | What the validator owns vs what the platform does |
| `Subnet/` | The `vividverse` Python package — contracts, incentive logic, validator modules |

---

## How It Works in One Paragraph

Miners register on chain and submit AI-generated video scenes through the platform UI — no local neuron required. Critics (tied to validator accounts) score each submission. The validator runs locally, pushes lifecycle heartbeats to the platform every ~10 seconds to drive round state, and at finalisation fetches the median scores from its critic pool, computes weights using a 70/30 winner/proportional split, and calls `set_weights()` on the Bittensor chain.

