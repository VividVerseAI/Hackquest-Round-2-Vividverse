"""
vividverse/contracts/tempo.py

Tempo completion check for round finalisation gate.

Bittensor subnet tempo = blocks per epoch. Yuma consensus produces final
aggregated scores only when the current tempo has fully completed (at the
step boundary). Round finalisation must not proceed until tempo completion.

blocks_since_last_step == 0  =>  at step boundary (previous tempo complete)
blocks_since_last_step > 0   =>  still in current tempo (not yet complete)
"""

import logging
from typing import TYPE_CHECKING, Tuple, Optional

if TYPE_CHECKING:
    from bittensor import Metagraph, Subtensor

_log = logging.getLogger(__name__)


def is_tempo_complete(
    metagraph: "Metagraph",
    subtensor: "Subtensor",
    netuid: int,
    deadline_block: Optional[int] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Check if the current Bittensor tempo has fully completed (at step boundary).

    Tempo is complete when blocks_since_last_step == 0, meaning we are at the
    start of a new tempo and the previous one has just finished. Until then,
    Yuma consensus may not have produced final aggregated scores.

    Args:
        metagraph: Synced Bittensor metagraph (call metagraph.sync() before).
        subtensor: Bittensor subtensor (used if metagraph lacks tempo attrs).
        netuid: Subnet UID.
        deadline_block: Optional epoch-deadline fallback. When the primary
            blocks_since_last_step check is unavailable or not yet zero, but the
            current block has reached or passed this number, finalisation proceeds
            anyway. Set this to the block number of the expected step boundary
            (computed at the moment all other criteria were first met). Ensures
            finalisation cannot stall if the exact blocks_since_last_step==0 frame
            is missed.

    Returns:
        (True, None) if tempo is complete and finalisation may proceed.
        (False, reason) if not complete; reason is logged and finalisation is skipped.
    """
    blocks_since = _get_blocks_since_last_step(metagraph, subtensor, netuid)
    tempo_val = _get_tempo(metagraph, subtensor, netuid)
    current_block = _get_block(metagraph, subtensor, netuid)

    if tempo_val is None or tempo_val <= 0:
        # Primary check impossible — try epoch-deadline fallback.
        if deadline_block is not None and current_block is not None and current_block >= deadline_block:
            _log.info(
                "tempo: primary tempo unavailable; epoch-deadline fallback triggered "
                "(current_block=%d >= deadline_block=%d). Proceeding with finalisation.",
                current_block,
                deadline_block,
            )
            return True, None
        return False, "tempo unavailable or invalid — cannot confirm step boundary"

    if blocks_since is None:
        # blocks_since_last_step missing — try epoch-deadline fallback.
        if deadline_block is not None and current_block is not None and current_block >= deadline_block:
            _log.info(
                "tempo: blocks_since_last_step unavailable; epoch-deadline fallback triggered "
                "(current_block=%d >= deadline_block=%d, tempo=%d). Proceeding with finalisation.",
                current_block,
                deadline_block,
                tempo_val,
            )
            return True, None
        return False, "blocks_since_last_step unavailable — cannot confirm tempo completion"

    if blocks_since != 0:
        # Not at step boundary yet — try epoch-deadline fallback.
        if deadline_block is not None and current_block is not None and current_block >= deadline_block:
            _log.info(
                "tempo: blocks_since_last_step=%d/%d (not zero), but epoch-deadline fallback "
                "triggered (current_block=%d >= deadline_block=%d). Proceeding with finalisation.",
                blocks_since,
                tempo_val,
                current_block,
                deadline_block,
            )
            return True, None
        return (
            False,
            f"tempo not yet completed — blocks_since_last_step={blocks_since}/{tempo_val}",
        )

    return True, None


def compute_next_epoch_block(
    metagraph: "Metagraph",
    subtensor: "Subtensor",
    netuid: int,
) -> Optional[int]:
    """
    Compute the block number of the next tempo step boundary from the current block.

    Used to set an epoch-deadline fallback: if the validator misses the exact
    blocks_since_last_step==0 frame, it can still finalise once this block is reached.

    Returns None if block or tempo information is unavailable.
    """
    block = _get_block(metagraph, subtensor, netuid)
    tempo_val = _get_tempo(metagraph, subtensor, netuid)
    if block is None or tempo_val is None or tempo_val <= 0:
        return None
    blocks_since = _get_blocks_since_last_step(metagraph, subtensor, netuid)
    if blocks_since is None:
        # Approximate via block % tempo.
        blocks_since = int(block % tempo_val)
    if blocks_since == 0:
        # Already at a boundary — next one is a full epoch away.
        return block + tempo_val
    return block + (tempo_val - blocks_since)


def _get_tempo(metagraph: "Metagraph", subtensor: "Subtensor", netuid: int) -> Optional[int]:
    """Extract tempo (blocks per epoch) from metagraph or subnet hyperparameters."""
    if hasattr(metagraph, "tempo"):
        t = getattr(metagraph, "tempo", None)
        if t is not None:
            return _to_int(t)
    try:
        hp = subtensor.get_subnet_hyperparameters(netuid)
        if hp is not None and hasattr(hp, "tempo"):
            t = hp.tempo
            return _to_int(t) if t is not None else None
    except Exception:
        pass
    return None


def _get_blocks_since_last_step(
    metagraph: "Metagraph", subtensor: "Subtensor", netuid: int
) -> Optional[int]:
    """
    Extract blocks_since_last_step from metagraph.

    Primary: reads metagraph.blocks_since_last_step or metagraph.blocks_since_step.

    Fallback: computes block % tempo as an approximation. This is approximate —
    it equals the true blocks_since_last_step only when tempo epochs are aligned
    to block 0, which is not guaranteed. Use the primary attributes when available.
    The fallback result of 0 (block divisible by tempo) is treated as a step boundary,
    which may fire one block early or late if alignment is off.
    """
    for attr in ("blocks_since_last_step", "blocks_since_step"):
        if hasattr(metagraph, attr):
            b = getattr(metagraph, attr, None)
            if b is not None:
                return _to_int(b)

    block = _get_block(metagraph, subtensor, netuid)
    tempo_val = _get_tempo(metagraph, subtensor, netuid)
    if block is not None and tempo_val is not None and tempo_val > 0:
        approx = int(block % tempo_val)
        _log.warning(
            "tempo: blocks_since_last_step not found on metagraph — "
            "falling back to block%%tempo approximation "
            "(block=%d, tempo=%d, approx=%d). "
            "This is only accurate when tempo epochs are aligned to block 0; "
            "finalisation may fire one block early or late.",
            block,
            tempo_val,
            approx,
        )
        return approx
    return None


def _get_block(metagraph: "Metagraph", subtensor: "Subtensor", netuid: int) -> Optional[int]:
    """Extract current block from metagraph or subtensor."""
    if hasattr(metagraph, "block"):
        bl = getattr(metagraph, "block", None)
        if bl is not None:
            return _to_int(bl)
    if hasattr(subtensor, "block"):
        bl = getattr(subtensor, "block", None)
        if bl is not None:
            return _to_int(bl)
    try:
        if hasattr(subtensor, "get_current_block"):
            bl = subtensor.get_current_block()
            if bl is not None:
                return _to_int(bl)
    except Exception:
        pass
    return None


def _to_int(value) -> Optional[int]:
    """Convert tensor or numeric to int."""
    if value is None:
        return None
    if hasattr(value, "item"):
        return int(value.item())
    if isinstance(value, (int, float)):
        return int(value)
    return None
