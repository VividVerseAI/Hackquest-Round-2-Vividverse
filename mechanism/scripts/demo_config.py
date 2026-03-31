"""
scripts/demo_config.py

Central configuration for the hackathon demo.
Edit MOCK_SUBMISSIONS to change what scores are demonstrated.
Edit DEMO_NETWORK to switch between mock/local/testnet.
"""

# ── Network ───────────────────────────────────────────────────────────────────
# "mock"    — no chain connection, full logic verified offline
# "local"   — local subtensor at ws://127.0.1:9944
# "test"    — Bittensor testnet (default for hackathon)
DEMO_NETWORK = "test"

# ── Wallet names (must match wallets created in BUILD.md setup.sh) ────────────
OWNER_WALLET_NAME     = "vv_owner"
VALIDATOR_WALLET_NAME = "vv_validator"
MINER_WALLET_NAME     = "vv_miner"
WALLET_HOTKEY         = "default"

# ── Subnet ────────────────────────────────────────────────────────────
# Set this to your registered netuid after running setup.sh testnet
NETUID = 1   # UPDATE THIS after subnet creation

# ── Demo round settings ───────────────────────────────────────────────────────
# Fast mode: skip real time windows, run full cycle immediately
FAST_MODE = True

# ── Mock submissions ──────────────────────────────────────────────────────────
# These simulate the submissions from 3 different miners.
# Each entry represents one miner's scene submission for the demo round.
#
# Designed to demonstrate all three emission outcomes:
#   - WINNER:      UID gets 70% + runner-up share (score >= 75 + highest)
#   - RUNNER_UP:   UID gets proportional runner-up share (score >= 75)
#   - BELOW_FLOOR: UID gets zero runner-up share (score < 75)
#
# In the real subnet, these scores come from human critics via enter_scores.py.
# In the demo, they are pre-set here to guarantee a clean, readable result.
MOCK_SUBMISSIONS = [
    {
        # Miner 0 — WINNER: strong narrative, excellent continuity
        "wallet_name": MINER_WALLET_NAME,
        "wallet_hotkey": WALLET_HOTKEY,
        "score": 88.50,
        "duration_seconds": 187.0,
        "label": "WINNER",
        "critic_evaluation": "Strong opening — clear character intent, advances plot",
    },
    {
        # Miner 1 — RUNNER-UP: above quality floor, gets proportional share
        # In a real multi-miner scenario this would be a different wallet.
# For the demo we use the same wallet with a different fake submission.
        "wallet_name": MINER_WALLET_NAME,
        "wallet_hotkey": WALLET_HOTKEY,
        "score": 76.00,
        "duration_seconds": 240.0,
        "label": "RUNNER-UP",
        "critic_evaluation": "Good continuity, emotional resonance slightly weak",
    },
    {
        # Miner 2 — BELOW FLOOR: does not meet quality threshold (75.00)
        "wallet_name": MINER_WALLET_NAME,
        "wallet_hotkey": WALLET_HOTKEY,
        "score": 60.00,
        "duration_seconds": 155.0,
        "label": "BELOW FLOOR",
        "critic_evaluation": "Continuity break — reset established characters",
    },
]

# ── Narrative context for round 0 ─────────────────────────────────────────────
# This is what gets broadcast to miners in the RoundStateQuery.
GENESIS_NARRATIVE = {
    "narrative_summary": (
        "An orbital research station, Helix-9, has gone silent. "
        "The crew of a retrieval vessel has just docked to find the station "
        "abandoned — lights still on, meals left half-eaten."
    ),
    "established_characters": (
        "Commander Yara Voss (pragmatic, conceals fear), "
        "Engineer Dak Mori (optimistic, youngest crew member)"
    ),
    "tone_and_genre": "Dark sci-fi thriller — desaturated palette, slow dread",
}