"""
Risk gate — position cap check, wallet score filter, slippage guard.
"""

import logging
from typing import Optional, Tuple

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)


class RiskGate:
    """Pass/fail checks before mirror execution."""

    def __init__(
        self,
        max_position_usd: float,
        min_score: int,
        slippage_tiers: dict,
        our_wallet_balance_usd: float = 0.0,
    ):
        self.max_position_usd = max_position_usd
        self.min_score = min_score
        self.slippage_tiers = slippage_tiers  # {"S": 2.0, "A": 1.5, "B": 1.0, "default": 0.8}
        self.our_balance_usd = our_wallet_balance_usd

    def get_slippage_bps(self, tier: str) -> int:
        # Config keys: tier_s, tier_a, tier_b, default
        config_key = f"tier_{tier.lower()}" if tier != "WATCH" else "default"
        pct = self.slippage_tiers.get(config_key) or self.slippage_tiers.get(tier) or self.slippage_tiers.get("default", 0.8)
        return int(float(pct) * 100)  # 1.5% -> 150 bps

    def check(self, swap: DetectedSwap, position_usd_estimate: float) -> Tuple[bool, Optional[str]]:
        """
        Returns (passed, reason). reason is None if passed.
        """
        score = getattr(swap, "wallet_score", None)
        if score is not None and score < self.min_score:
            return False, f"Wallet score {swap.wallet_score} < min {self.min_score}"

        if position_usd_estimate > self.max_position_usd:
            return False, f"Position ${position_usd_estimate:.0f} exceeds cap ${self.max_position_usd:.0f}"

        if self.our_balance_usd > 0 and position_usd_estimate > self.our_balance_usd:
            return False, f"Position exceeds wallet balance"

        return True, None
