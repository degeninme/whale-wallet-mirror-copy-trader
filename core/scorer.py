"""
Wallet scoring engine — win-rate, recency weighted.
Per-wallet tier from config; live rescoring uses on-chain history (simplified for beta).
"""

import logging
from typing import Dict, Optional

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)


class WalletScorer:
    """Assign tier and score to wallets. Config-based for public build."""

    def __init__(self, wallet_tiers: Dict[str, str], min_score_to_mirror: int = 65):
        # wallet_tiers: address -> "S" | "A" | "B" | "watch"
        self.wallet_tiers = {k.lower(): v.upper() for k, v in wallet_tiers.items()}
        self.min_score = min_score_to_mirror
        self._tier_score = {"S": 95, "A": 85, "B": 70, "WATCH": 50}

    def get_tier(self, address: str) -> str:
        return self.wallet_tiers.get(address.lower(), "B")

    def get_score(self, address: str) -> int:
        tier = self.get_tier(address)
        return self._tier_score.get(tier.upper(), 65)

    def should_mirror(self, address: str) -> bool:
        return self.get_score(address) >= self.min_score

    def enrich_swap(self, swap: DetectedSwap) -> DetectedSwap:
        """Add wallet_tier and score to swap."""
        tier = self.get_tier(swap.wallet_address)
        score = self.get_score(swap.wallet_address)
        swap.wallet_tier = tier  # type: ignore
        swap.wallet_score = score  # type: ignore
        return swap
