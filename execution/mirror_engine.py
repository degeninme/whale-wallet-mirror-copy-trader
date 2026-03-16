"""
Mirror engine — constructs mirror tx, applies proportional sizing, gas optimization.
Paper mode: simulates and logs. Live mode: builds and broadcasts.
"""

import logging
from datetime import datetime
from typing import Optional

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)


class MirrorEngine:
    """Build and optionally execute mirror trades."""

    def __init__(
        self,
        mirror_scale: float,
        is_paper: bool,
        solana_executor: Optional[object] = None,
        base_executor: Optional[object] = None,
    ):
        self.mirror_scale = mirror_scale
        self.is_paper = is_paper
        self.solana_executor = solana_executor
        self.base_executor = base_executor

    def execute_mirror(
        self,
        swap: DetectedSwap,
        slippage_bps: int,
    ) -> Optional[str]:
        """
        Execute or simulate mirror. Returns tx_hash if live, None if paper.
        """
        scaled_from = swap.from_amount * self.mirror_scale
        scaled_to = swap.to_amount * self.mirror_scale

        if self.is_paper:
            from_disp = swap.from_token[:20] + ".." if len(swap.from_token) > 20 else swap.from_token
            to_disp = swap.to_token[:20] + ".." if len(swap.to_token) > 20 else swap.to_token
            logger.info(
                "[PAPER] Would mirror: %s %s -> %s %s (scale %.2fx)",
                scaled_from,
                from_disp,
                scaled_to,
                to_disp,
                self.mirror_scale,
            )
            return None

        if swap.chain == Chain.SOLANA and self.solana_executor:
            return self.solana_executor.execute(swap, scaled_from, scaled_to, slippage_bps)
        if swap.chain == Chain.BASE and self.base_executor:
            return self.base_executor.execute(swap, scaled_from, scaled_to, slippage_bps)

        logger.warning("No executor for chain %s", swap.chain)
        return None
