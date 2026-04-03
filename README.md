# Vividverse — Hackquest Round 2

**Proof of Cinematic Intelligence — Bittensor Subnet 210**

Vividverse is a Bittensor subnet where AI filmmakers compete each round to produce the best next scene of a collaboratively evolving film. Validators read critic scores from the platform and call `set_weights()` on-chain to distribute emissions.

**Platform:** https://staging.vividverse.ai · **Network:** Bittensor testnet · **Netuid:** 210

**Updated Proposal:** https://generated-daisy-98b.notion.site/ROUND-2-Vividverse-Subnet-Proposal-331975636e1980129830eae1e289ba51?pvs=74

**Architecture Intro:** https://1drv.ms/v/c/86e178da39acda17/IQCcuJaSgPhMR5bxZgG7QeK5AUSSgNodIvpCTT5S7XBYIko?e=pSuyyB

**Medium Article:** https://medium.com/@vividverse/what-happened-to-movie-magic-0f8447240123

**Pitch Video:**: https://www.youtube.com/watch?v=VyI014FRu00&feature=youtu.be

**Demo Video:**: https://www.youtube.com/watch?v=_UDRUKD1prQ

---

> **Note:** If you cloned this repository before 2 April 2026, please delete your local copy and clone again — several validator fixes have been applied since then.
> ```bash
> git clone https://github.com/VividVerseAI/Hackquest-Round-2-Vividverse.git
> ```

---

Quick note: while the demo video didn't showcase the video stitching (previous round's video appended to the chain of video clips) and the /preview page those are indeed operational; the demo only serves to illistrate the mechanism in operation and lifecycle management.

Also - Hippius S3 storage is also storing video and image assets.

## Quick Start

**Requires Docker only.**

```bash
git clone https://github.com/VividVerseAI/Hackquest-Round-2-Vividverse.git
cd Hackquest-Round-2-Vividverse
docker build -t vividverse-validator .
docker run vividverse-validator
```

See **[Run & Setup.md](Run%20&%20Setup.md)** for full details — switching validators, signing in to the platform, and critic credentials.

---

## File Index

| File / Directory | What It Is |
|---|---|
| [Run & Setup.md](Run%20&%20Setup.md) | **Start here.** Docker run commands, platform sign-in guide, validator credentials, critic logins |
| [Dockerfile](Dockerfile) | Self-contained validator image — pre-bakes wallets and all dependencies |
| [mechanism/README.md](mechanism/README.md) | How the mechanism works — round phases, incentive logic, validator role, repo structure |
| [mechanism/neurons/validator.py](mechanism/neurons/validator.py) | Validator entry point — phase routing, heartbeat, `set_weights` |
| [mechanism/docs/ARCHITECTURE_BITTENSOR_PLATFORM.md](mechanism/docs/ARCHITECTURE_BITTENSOR_PLATFORM.md) | How Vividverse uses Bittensor — where it differs from the standard template (no miner axon) |
| [mechanism/docs/MECHANISM_PLATFORM_BOUNDARY.md](mechanism/docs/MECHANISM_PLATFORM_BOUNDARY.md) | What the validator owns vs what the platform does |
| \`Subnet/\` | The \`vividverse\` Python package — contracts, incentive logic, validator modules |

---

## How It Works in One Paragraph

Miners register on chain and submit AI-generated video scenes through the platform UI — no local neuron required. Critics (tied to validator accounts) score each submission. The validator runs locally, pushes lifecycle heartbeats to the platform every ~10 seconds to drive round state, and at finalisation fetches the median scores from its critic pool, computes weights using a 70/30 winner/proportional split, and calls `set_weights()` on the Bittensor chain.
