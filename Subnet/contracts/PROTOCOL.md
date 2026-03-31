# Vividverse Protocol — Extraction-Ready Module

This package contains the **subnet-owner controlled** mechanism logic. It is structured so that it can be extracted into a separate package or private repository without breaking participant compatibility.

## Design Principle

**Open participation**: Validators and miners can participate without using the platform. The protocol is public and runnable. Consolidation here prepares for future flexibility (e.g. publishing a standalone `vividverse-protocol` package) without requiring a private dependency.

## Extraction-Ready Modules

These modules define mechanism rules with **no env overrides**. The subnet owner controls them by what they ship.

| Module | Contents | Deps |
|--------|----------|-----|
| `cadence.py` | Submission/eval/prompt-voting windows, miner count for countdown | None |
| `round_registry.py` | Round deadlines, creation identity | cadence |
| `prompt_voting_completion.py` | Quorum, completion rules, prompt selection logic | None |
| `incentive.py` | QUALITY_THRESHOLD, compute_weights, emission shares | torch |

## Extraction Path (Future)

1. Create a new repo `vividverse-protocol` (or keep private: `vividverse-protocol-internal`)
2. Copy these modules + their type dependencies (`scoring`, `phases` if needed)
3. Publish as `pip install vividverse-protocol` (public) or private registry
4. Main repo: `dependencies = ["vividverse-protocol @ ..."]`
5. Neurons import from `vividverse_protocol` instead of `vividverse.contracts`

**Important**: For open participation, the protocol package must remain publicly installable. A private package would block validators who don't have access.

## What Stays in Main Repo

- Neurons (`validator.py`, miner) — entry points that import from contracts
- Platform integration (`platform_fetch`, `consensus_fetch`)
- Utils, submission store, canonical chain

## Related

- `docs/MECHANISM_CONTRACTS.md` — contract semantics
- `docs/NEXT_PHASE_VALIDATOR_AUTHORITY.md` — validator/platform boundary
