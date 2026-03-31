#!/usr/bin/env python3
"""
scripts/sign_challenge.py

Sign a Vividverse platform challenge message with a btcli hotkey.
Use this when your hotkey exists only in Bittensor (btcli) wallets, not in Polkadot.js/Talisman.

WALKTHROUGH
------------

1. WHERE TO GET THE MESSAGE
   In the platform (signin / signup / verify-hotkey-access page):
   - Enter your hotkey (SS58 address) and click Continue
   - A grey message box appears; click "Copy message" (or copy the text manually)
   - The message looks like: "Vividverse sign-in: <timestamp>-<random>"

2. RUN THIS COMMAND
   From the mechanism folder:

     python scripts/sign_challenge.py --message "PASTE_MESSAGE_HERE" --wallet.name YOUR_COLDKEY --wallet.hotkey YOUR_HOTKEY

   Example:

     python scripts/sign_challenge.py --message "Vividverse sign-in: 1742345678-a1b2c3d4" --wallet.name miner --wallet.hotkey default

   The message must match exactly (no extra spaces or newlines). Wrap it in quotes.

3. WHERE TO PASTE THE SIGNATURE
   The script prints a hex string (0x...).
   In the platform, scroll down past "Sign with Polkadot.js / Talisman".
   Paste the hex into the "paste hex signature" text area and click Submit signature.
"""

from __future__ import annotations
import argparse
import sys
import os

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    # Parse before importing bittensor (bt import can affect argparse)
    parser = argparse.ArgumentParser(
        description="Sign a Vividverse challenge message with a btcli hotkey. Paste the output hex into the platform."
    )
    parser.add_argument("--message", "-m", required=True, help="Exact challenge message from the platform")
    parser.add_argument("--wallet.name", dest="wallet_name", default="miner", help="Wallet coldkey name (default: miner)")
    parser.add_argument("--wallet.hotkey", dest="wallet_hotkey", default="default", help="Wallet hotkey name (default: default)")
    args = parser.parse_args()

    try:
        import bittensor as bt
    except ImportError:
        print("Error: bittensor not installed. Run: pip install bittensor", file=sys.stderr)
        return 1

    message = args.message.strip()
    if not message:
        print("Error: --message cannot be empty", file=sys.stderr)
        return 1

    # Build minimal config for bt.Wallet (Bittensor v10 uses PascalCase)
    from argparse import Namespace
    config = Namespace()
    config.wallet = Namespace()
    config.wallet.name = args.wallet_name
    config.wallet.hotkey = args.wallet_hotkey
    config.wallet.path = os.path.expanduser("~/.bittensor/wallets/")

    try:
        wallet = bt.Wallet(config=config)
    except Exception as e:
        print(f"Error: Could not load wallet '{args.wallet_name}' / hotkey '{args.wallet_hotkey}': {e}", file=sys.stderr)
        print("Create it with: btcli wallet create --wallet.name <name> && btcli wallet new_hotkey --wallet.name <name> --wallet.hotkey <hotkey>", file=sys.stderr)
        return 1

    keypair = wallet.hotkey
    if not hasattr(keypair, "sign"):
        print("Error: Hotkey keypair has no sign method. Check bittensor version.", file=sys.stderr)
        return 1

    try:
        message_bytes = message.encode("utf-8")
        sig_bytes = keypair.sign(message_bytes)
    except Exception as e:
        print(f"Error: Signing failed: {e}", file=sys.stderr)
        return 1

    if not sig_bytes or len(sig_bytes) < 64:
        print("Error: Invalid signature format (expected 64 bytes).", file=sys.stderr)
        return 1

    hex_sig = "0x" + sig_bytes.hex()
    print(hex_sig)
    print("", file=sys.stderr)
    print("→ Paste the above hex into the platform's 'paste signature' field and click Submit.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
