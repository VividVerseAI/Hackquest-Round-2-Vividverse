#!/usr/bin/env python3
"""
dump_testnet_wallets.py

Dumps hotkey, coldkey, and mnemonic for all testnet wallets.
Labels each wallet as: validator / miner / critic / unknown
based on the wallet folder name convention.

Output: ./testnet_wallets.txt  (also printed to stdout)

Usage:
    python scripts/dump_testnet_wallets.py
    python scripts/dump_testnet_wallets.py --password yourpassword
    python scripts/dump_testnet_wallets.py --wallets-dir /path/to/wallets
"""

import os
import sys
import argparse
import getpass
from pathlib import Path

try:
    import bittensor as bt
except ImportError:
    print("ERROR: bittensor not installed. Run: pip install bittensor")
    sys.exit(1)


# ── Role detection from wallet name ──────────────────────────────────────────

def detect_role(wallet_name: str) -> str:
    name = wallet_name.lower()
    if "validator" in name:
        return "validator"
    if "miner" in name:
        return "miner"
    if "critic" in name:
        return "critic"
    return "unknown"


# ── Decrypt a keyfile, return (ss58_address, mnemonic) or None on failure ────

def decrypt_keyfile(keyfile, password: str):
    import json

    def _parse(raw: bytes):
        data = json.loads(raw.decode())
        ss58 = data.get("ss58Address") or data.get("publicKey")
        mnemonic = (
            data.get("secretPhrase")
            or data.get("mnemonic")
            or data.get("secret_phrase")
        )
        return ss58, mnemonic

    # If the keyfile is not encrypted (typical for hotkeys), read raw JSON directly.
    try:
        if not keyfile.is_encrypted():
            return _parse(keyfile.data)
    except Exception:
        pass

    # Encrypted — try passwords: empty string, "thinker", then any extra password provided.
    candidates = ["", "thinker"]
    if password and password not in candidates:
        candidates.append(password)

    for pwd in candidates:
        try:
            raw = bt.decrypt_keyfile_data(keyfile.data, pwd)
            return _parse(raw)
        except Exception:
            continue

    return None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Dump testnet wallet keys and mnemonics")
    parser.add_argument("--password", help="Wallet encryption password (prompted if omitted)")
    parser.add_argument(
        "--wallets-dir",
        default=str(Path.home() / ".bittensor" / "wallets"),
        help="Path to bittensor wallets directory",
    )
    parser.add_argument(
        "--output",
        default="testnet_wallets.txt",
        help="Output file path (default: testnet_wallets.txt)",
    )
    args = parser.parse_args()

    wallets_dir = Path(args.wallets_dir)
    if not wallets_dir.exists():
        print(f"ERROR: Wallets directory not found: {wallets_dir}")
        sys.exit(1)

    password = args.password
    if password is None:
        password = getpass.getpass("Wallet password (press Enter for none): ")

    wallet_names = sorted(
        d for d in os.listdir(wallets_dir)
        if (wallets_dir / d).is_dir() and not d.startswith(".")
    )

    lines = []
    lines.append("=" * 70)
    lines.append("  VIVIDVERSE TESTNET WALLETS")
    lines.append("=" * 70)

    for wallet_name in wallet_names:
        role = detect_role(wallet_name)
        w = bt.Wallet(name=wallet_name, path=str(wallets_dir))

        # ── Coldkey ──────────────────────────────────────────────────────────
        coldkey_ss58 = None
        coldkey_mnemonic = None
        if w.coldkey_file.exists_on_device():
            coldkey_ss58, coldkey_mnemonic = decrypt_keyfile(w.coldkey_file, password)
            if coldkey_ss58 is None:
                # Try reading public address from coldkeypub.txt
                pub_path = wallets_dir / wallet_name / "coldkeypub.txt"
                if pub_path.exists():
                    try:
                        import json
                        data = json.loads(pub_path.read_text())
                        coldkey_ss58 = data.get("ss58Address") or data.get("publicKey")
                    except Exception:
                        coldkey_ss58 = pub_path.read_text().strip()

        # ── Hotkeys ──────────────────────────────────────────────────────────
        hotkeys_dir = wallets_dir / wallet_name / "hotkeys"
        hotkey_names = []
        if hotkeys_dir.exists():
            hotkey_names = sorted(
                f for f in os.listdir(hotkeys_dir)
                if not f.endswith(".txt") and not f.startswith(".")
                and (hotkeys_dir / f).is_file()
            )

        lines.append("")
        lines.append(f"┌─ [{role.upper()}]  {wallet_name}")
        lines.append(f"│  Coldkey SS58 : {coldkey_ss58 or '(could not decrypt)'}")
        if coldkey_mnemonic:
            lines.append(f"│  Coldkey mnemonic : {coldkey_mnemonic}")
        else:
            lines.append(f"│  Coldkey mnemonic : (wrong password or not stored)")

        for hk_name in hotkey_names:
            w2 = bt.Wallet(name=wallet_name, hotkey=hk_name, path=str(wallets_dir))
            hk_ss58, hk_mnemonic = None, None
            if w2.hotkey_file.exists_on_device():
                hk_ss58, hk_mnemonic = decrypt_keyfile(w2.hotkey_file, password)
                if hk_ss58 is None:
                    pub_path = hotkeys_dir / f"{hk_name}pub.txt"
                    if pub_path.exists():
                        try:
                            import json
                            data = json.loads(pub_path.read_text())
                            hk_ss58 = data.get("ss58Address") or data.get("publicKey")
                        except Exception:
                            hk_ss58 = pub_path.read_text().strip()

            lines.append(f"│")
            lines.append(f"│  ── hotkey: {hk_name}")
            lines.append(f"│     SS58     : {hk_ss58 or '(could not decrypt)'}")
            if hk_mnemonic:
                lines.append(f"│     mnemonic : {hk_mnemonic}")
            else:
                lines.append(f"│     mnemonic : (wrong password or not stored)")

        lines.append(f"└{'─' * 60}")

    lines.append("")
    lines.append("=" * 70)

    output = "\n".join(lines)
    print(output)

    out_path = Path(args.output)
    out_path.write_text(output)
    print(f"\n✓ Saved to {out_path.resolve()}")


if __name__ == "__main__":
    main()
