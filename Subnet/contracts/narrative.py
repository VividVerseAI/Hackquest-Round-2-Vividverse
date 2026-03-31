"""
vividverse/contracts/narrative.py

Canonical mechanism narrative state model.
Vividverse is story-driven; narrative context is central.
The mechanism defines what narrative state means; platform stores/displays a copy.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class ContinuityReference:
    """
    Reference to prior-round content for sequential generation.
    Character/scene/object continuity; used to maintain narrative consistency.
    """
    entity_type: str  # character | place | object
    entity_tag: str
    storage_ref: str
    media_type: str = "image"  # image | audio
    key_angle: Optional[str] = None  # front | three_quarter | close_up | object | scene


@dataclass
class RoundNarrativeContext:
    """
    Narrative context for a single round — what miners receive and build upon.
    Maps to RoundStateQuery and Round DB fields.
    """
    round_id: int
    narrative_summary: str
    established_characters: str
    tone_and_genre: str
    selected_prompt_id: Optional[str] = None
    continuity_refs: Optional[List[ContinuityReference]] = None


@dataclass
class NarrativeState:
    """
    Full narrative state — current story position, prior context, prompt lineage.
    Validators use this to judge narrative consistency.
    """
    round_id: int
    round_narrative: RoundNarrativeContext
    # 0-100: where the story is (miner-reported or inferred)
    narrative_progression: Optional[int] = None
    # Prior canonical chain context
    canonical_chain_length: int = 0
    canonical_chain_hash: str = ""
    # Prompt lineage: which prompt drove this round
    prompt_id: Optional[str] = None
    prompt_content: Optional[str] = None
