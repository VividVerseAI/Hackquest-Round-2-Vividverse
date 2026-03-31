"""
vividverse/contracts/artifact.py

Canonical artifact reference model — mechanism-side.
Video outputs and continuity references use this.
Platform may store URLs; future storage (Hippius/IPFS) can plug in.

Storage today: local path, platform URL, object store.
Future: decentralized storage references.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ArtifactRefKind(str, Enum):
    """Storage backend for the artifact. Defines how to resolve the reference."""
    LOCAL = "local"           # File path on disk
    PLATFORM = "platform"    # Platform-hosted URL (temporary)
    OBJECT_STORE = "object_store"  # S3/GCS URL
    # Future:
    # IPFS = "ipfs"
    # HIPPIUS = "hippius"


@dataclass(frozen=True)
class ArtifactRef:
    """
    Mechanism-side artifact reference.
    Miners produce video outputs; validators score them.
    The reference tells where to fetch the actual bytes.
    """
    ref: str
    kind: ArtifactRefKind
    content_type: str = "video/mp4"
    checksum_sha256: Optional[str] = None
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None

    def is_resolvable(self) -> bool:
        """True if the reference can be resolved (e.g. URL or known path)."""
        return bool(self.ref and len(self.ref) > 0)

    def as_url(self) -> Optional[str]:
        """Return URL if this is a URL-based ref. None for local paths."""
        if self.kind in (ArtifactRefKind.PLATFORM, ArtifactRefKind.OBJECT_STORE):
            return self.ref
        return None
