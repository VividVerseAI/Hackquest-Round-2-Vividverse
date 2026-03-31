#!/usr/bin/env bash
#
# scripts/setup.sh
#
# Environment setup script for Vividverse subnet.
# See docs/TESTNET_SETUP.md for full testnet guide.
#
# Usage:
#   bash scripts/setup.sh env         # Create wallets
#   bash scripts/setup.sh local       # Register on localnet
#   bash scripts/setup.sh testnet     # Register on testnet
#   bash scripts/setup.sh stake       # Stake validator (testnet, run after testnet)
#

set -e

# Configuration
WALLET_NAME_MINER="miner"
WALLET_NAME_VALIDATOR="validator"
HOTKEY_NAME="default"
NETUID_LOCAL=1
NETUID_TESTNET=210  # vividverse subnet on testnet
STAKE_AMOUNT=100   # TAO to stake for validator permit (testnet)

# Function: Create and fund wallets
setup_env() {
    echo "=== Creating wallets ==="
    
    # Create miner wallet
    if ! btcli wallet list | grep -q "$WALLET_NAME_MINER"; then
        echo "Creating miner wallet..."
        btcli wallet create --wallet.name "$WALLET_NAME_MINER" --no_password
        echo "Creating miner hotkey..."
        btcli wallet new_hotkey --wallet.name "$WALLET_NAME_MINER" --wallet.hotkey "$HOTKEY_NAME" --no_password 2>/dev/null || btcli wallet new_hotkey --wallet.name "$WALLET_NAME_MINER" --wallet.hotkey "$HOTKEY_NAME"
    else
        echo "Miner wallet already exists"
    fi
    
    # Create validator wallet
    if ! btcli wallet list | grep -q "$WALLET_NAME_VALIDATOR"; then
        echo "Creating validator wallet..."
        btcli wallet create --wallet.name "$WALLET_NAME_VALIDATOR" --no_password
        echo "Creating validator hotkey..."
        btcli wallet new_hotkey --wallet.name "$WALLET_NAME_VALIDATOR" --wallet.hotkey "$HOTKEY_NAME" --no_password 2>/dev/null || btcli wallet new_hotkey --wallet.name "$WALLET_NAME_VALIDATOR" --wallet.hotkey "$HOTKEY_NAME"
    else
        echo "Validator wallet already exists"
    fi
    
    echo ""
    echo "=== Wallet addresses ==="
    echo "Miner coldkey:"
    btcli wallet overview --wallet.name "$WALLET_NAME_MINER"
    echo ""
    echo "Validator coldkey:"
    btcli wallet overview --wallet.name "$WALLET_NAME_VALIDATOR"
    echo ""
    echo "Fund these addresses from the faucet before proceeding."
}

# Function: Register on localnet
setup_local() {
    echo "=== Registering on localnet ==="
    
    # Register miner
    echo "Registering miner..."
    btcli subnet register \
        --wallet.name "$WALLET_NAME_MINER" \
        --wallet.hotkey "$HOTKEY_NAME" \
        --netuid "$NETUID_LOCAL" \
        --subtensor.network local \
        --no_prompt
    
    # Register validator
    echo "Registering validator..."
    btcli subnet register \
        --wallet.name "$WALLET_NAME_VALIDATOR" \
        --wallet.hotkey "$HOTKEY_NAME" \
        --netuid "$NETUID_LOCAL" \
        --subtensor.network local \
        --no_prompt
    
    echo "=== Registration complete ==="
    echo "Start miner: python neurons/miner.py --netuid $NETUID_LOCAL --subtensor.network local --wallet.name $WALLET_NAME_MINER --wallet.hotkey $HOTKEY_NAME"
    echo "Start validator: python neurons/validator.py --netuid $NETUID_LOCAL --subtensor.network local --wallet.name $WALLET_NAME_VALIDATOR --wallet.hotkey $HOTKEY_NAME"
}

# Function: Register on testnet
setup_testnet() {
    echo "=== Registering on testnet (netuid=$NETUID_TESTNET) ==="
    echo "Ensure wallets are funded with test TAO. Request from Bittensor Discord."
    echo ""
    
    # Register miner
    echo "Registering miner..."
    btcli subnet register \
        --wallet.name "$WALLET_NAME_MINER" \
        --wallet.hotkey "$HOTKEY_NAME" \
        --netuid "$NETUID_TESTNET" \
        --network test
    
    # Register validator
    echo "Registering validator..."
    btcli subnet register \
        --wallet.name "$WALLET_NAME_VALIDATOR" \
        --wallet.hotkey "$HOTKEY_NAME" \
        --netuid "$NETUID_TESTNET" \
        --network test
    
    echo ""
    echo "=== Registration complete ==="
    echo "Next: Run 'bash scripts/setup.sh stake' to stake validator (required for validator permit)"
    echo ""
    echo "Start miner:"
    echo "  python neurons/miner.py --netuid $NETUID_TESTNET --subtensor.network test --wallet.name $WALLET_NAME_MINER --wallet.hotkey $HOTKEY_NAME"
    echo ""
    echo "Start validator (after staking):"
    echo "  python neurons/validator.py --netuid $NETUID_TESTNET --subtensor.network test --wallet.name $WALLET_NAME_VALIDATOR --wallet.hotkey $HOTKEY_NAME"
}

# Function: Stake validator for testnet (required for validator permit)
setup_stake() {
    echo "=== Staking validator for testnet ==="
    echo "Validators need stake weight >= 1000 for a validator permit."
    echo "Staking $STAKE_AMOUNT TAO to validator hotkey..."
    echo ""
    
    btcli stake add \
        --netuid "$NETUID_TESTNET" \
        --wallet.name "$WALLET_NAME_VALIDATOR" \
        --wallet.hotkey "$HOTKEY_NAME" \
        --amount "$STAKE_AMOUNT" \
        --network test
    
    echo ""
    echo "=== Stake complete ==="
    echo "Verify: btcli wallet overview --netuid $NETUID_TESTNET --wallet.name $WALLET_NAME_VALIDATOR --network test"
    echo "Check VPERMIT is set (validator permit)."
}

# Main
COMMAND=${1:-env}

case "$COMMAND" in
    env)
        setup_env
        ;;
    local)
        setup_local
        ;;
    testnet)
        setup_testnet
        ;;
    stake)
        setup_stake
        ;;
    *)
        echo "Usage: $0 {env|local|testnet|stake}"
        echo "  env     - Create wallets"
        echo "  local   - Register on local subtensor"
        echo "  testnet - Register on Bittensor testnet"
        echo "  stake   - Stake validator (testnet, run after testnet)"
        exit 1
        ;;
esac