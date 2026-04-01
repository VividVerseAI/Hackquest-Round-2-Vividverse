# Mechanism–Platform Boundary

**Principle:** The subnet/mechanism is the functionality. The platform is the interface. The platform never performs subnet functions—it receives, stores, and displays. See `project/docs/MIGRATION_FRONTEND_SUBNET_ALIGNMENT.md`.

## What the Validator Owns (Mechanism-Backed)

- Prompt voting completion and selected prompt
- Round creation (via roundBootstrap push)
- Phase transitions (submission → evaluation → finalised)
- Restart decision (all scores < QUALITY_THRESHOLD)
- Deadlines (submission, evaluation, prompt voting)
- `compute_weights`, `set_weights` on chain
- Blacklist: only registered validators may query miners

## What the Platform Does

- **Receives** — Validator pushes to `POST /api/validator/lifecycle`
- **Stores** — Rounds, phases, prompts, scores, submissions
- **Serves** — `GET /api/subnet/state`, `GET /api/rounds/current`, `GET /api/subnet/rounds/{id}/state`, `GET /api/subnet/rounds/{id}/scores`
- **UX** — Voting UI, submission upload, critic scoring

## What the Platform Must NOT Do

- Create rounds (except when persisting validator roundBootstrap)
- Advance phases (scheduler, skip, cron)
- Select prompt
- Decide restart
- Compute winner or weights
- Set deadlines

## APIs Used by Validator

| API | Purpose |
|-----|---------|
| `GET /api/rounds/current` | Current roundId (from subnet state) |
| `GET /api/subnet/rounds/{roundId}/state` | Round phase, deadlines, narrative |
| `GET /api/subnet/rounds/{roundId}/scores` | Critic scores → weights → set_weights |
| `GET /api/subnet/prompt-votes` | Prompt votes → validator selects winner |
| `POST /api/validator/lifecycle` | Push phase, round, restart, scoringOutcome |

## Miner Submission Ownership

**The miner is the authoritative source for submission metadata.** It owns the lifecycle, storage, and consistency of submissions.

- **Miner local store** — SQLite (`miner_submissions.db`) stores hash, URL, duration, etc. per round/hotkey. The miner always responds to SubmissionSynapse from this store.
- **Platform** — Secondary. When users submit via the platform UI, the platform stores for its own UI/scoring. The miner syncs from `GET /api/miner/metadata` into its local store when configured with `PLATFORM_API_URL`. Platform = input channel; miner = authoritative for validator queries.
- **Terminal mode** — Miners can register submissions directly via `scripts/add_miner_submission.py` or by calling `vividverse.miner.submission_store.upsert_submission`.
