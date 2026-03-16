"""
Wallet scoring engine — win-rate, recency weighted.
Per-wallet tier from config; optimized with O(1) lookups and single-pass enrich.
"""

import logging
from typing import Dict, Optional

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)

# Immutable tier->score map. Default 65 for unknown.
TIER_SCORES = {"S": 95, "A": 85, "B": 70, "WATCH": 50}
DEFAULT_SCORE = 65


class WalletScorer:
    """Assign tier and score to wallets. O(1) lookups, single normalization."""

    __slots__ = ("_tiers", "min_score", "_tier_score")

    def __init__(self, wallet_tiers: Dict[str, str], min_score_to_mirror: int = 65):
        # Normalize once at init: lowercase keys, uppercase tier values
        self._tiers = {
            k.strip().lower(): (v or "").strip().upper() or "B"
            for k, v in wallet_tiers.items()
            if k and str(k).strip()
        }
        self.min_score = min_score_to_mirror
        self._tier_score = TIER_SCORES

    def get_tier(self, address: str) -> str:
        return self._tiers.get((address or "").lower(), "B")

    def get_score(self, address: str) -> int:
        return self._tier_score.get(self.get_tier(address), DEFAULT_SCORE)

    def should_mirror(self, address: str) -> bool:
        return self.get_score(address) >= self.min_score

    def enrich_swap(self, swap: DetectedSwap) -> DetectedSwap:
        """Single-pass: tier + score in one lookup cycle."""
        tier = self.get_tier(swap.wallet_address)
        swap.wallet_tier = tier  # type: ignore
        swap.wallet_score = self._tier_score.get(tier, DEFAULT_SCORE)  # type: ignore
        return swap
