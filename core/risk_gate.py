"""
Risk gate — position cap check, wallet score filter, slippage guard.
Optimized with fail-fast checks and memoized slippage lookup.
"""

import logging
from typing import Optional, Tuple

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)
DEFAULT_SLIPPAGE_BPS = 80  # 0.8% fallback


class RiskGate:
    """Pass/fail checks before mirror execution. Fail-fast validation order."""

    __slots__ = ("max_position_usd", "min_score", "slippage_tiers", "our_balance_usd", "_slippage_cache")

    def __init__(
        self,
        max_position_usd: float,
        min_score: int,
        slippage_tiers: dict,
        our_wallet_balance_usd: float = 0.0,
    ):
        self.max_position_usd = max_position_usd
        self.min_score = min_score
        self.slippage_tiers = slippage_tiers or {}
        self.our_balance_usd = our_wallet_balance_usd
        self._slippage_cache: dict[str, int] = {}

    def get_slippage_bps(self, tier: str) -> int:
        """Memoized tier->bps lookup. O(1) for repeated tier queries."""
        if tier in self._slippage_cache:
            return self._slippage_cache[tier]
        config_key = f"tier_{tier.lower()}" if tier and tier != "WATCH" else "default"
        pct = (
            self.slippage_tiers.get(config_key)
            or self.slippage_tiers.get(tier)
            or self.slippage_tiers.get("default", 0.8)
        )
        try:
            bps = int(float(pct) * 100)
        except (TypeError, ValueError):
            bps = DEFAULT_SLIPPAGE_BPS
        self._slippage_cache[tier] = bps
        return bps

    def check(self, swap: DetectedSwap, position_usd_estimate: float) -> Tuple[bool, Optional[str]]:
        """Returns (passed, reason). Fail-fast: cheapest checks first."""
        # 1. Fast: reject zero/negative position
        if position_usd_estimate is not None and position_usd_estimate <= 0:
            return False, "Position size must be positive"
        # 2. Score filter
        score = getattr(swap, "wallet_score", None)
        if score is not None and score < self.min_score:
            return False, f"Wallet score {score} < min {self.min_score}"
        # 3. Position cap
        if position_usd_estimate > self.max_position_usd:
            return False, f"Position ${position_usd_estimate:.0f} exceeds cap ${self.max_position_usd:.0f}"
        # 4. Balance check
        if self.our_balance_usd > 0 and position_usd_estimate > self.our_balance_usd:
            return False, "Position exceeds wallet balance"
        return True, None
