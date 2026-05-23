"""
Mirror engine — constructs mirror tx, applies fixed or proportional sizing.
Paper mode: simulates and logs. Live mode: builds and broadcasts.
"""

import logging
import os
from typing import Optional

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)


class MirrorEngine:
    """Build and optionally execute mirror trades."""

    def __init__(
        self,
        mirror_scale: float,
        is_paper: bool,
        fixed_trade_usd: Optional[float] = None,
    ):
        self.mirror_scale    = mirror_scale
        self.is_paper        = is_paper
        self.fixed_trade_usd = fixed_trade_usd

    def _scale_amounts(self, swap: DetectedSwap):
        """Return (scaled_from, scaled_to) based on fixed USD or mirror_scale."""
        if self.fixed_trade_usd and swap.from_amount_usd and swap.from_amount_usd > 0:
            ratio = self.fixed_trade_usd / swap.from_amount_usd
            return swap.from_amount * ratio, swap.to_amount * ratio
        return swap.from_amount * self.mirror_scale, swap.to_amount * self.mirror_scale

    def execute_mirror(
        self,
        swap: DetectedSwap,
        slippage_bps: int,
    ) -> Optional[str]:
        """Execute or simulate mirror. Returns tx_hash if live, None if paper."""
        scaled_from, scaled_to = self._scale_amounts(swap)
        trade_usd = self.fixed_trade_usd or round((swap.from_amount_usd or 0) * self.mirror_scale, 2)

        if self.is_paper:
            from_disp = swap.from_token[:20] + ".." if len(swap.from_token) > 20 else swap.from_token
            to_disp   = swap.to_token[:20]   + ".." if len(swap.to_token)   > 20 else swap.to_token
            logger.info("[PAPER] Would mirror: %.4f %s -> %.2f %s (~$%.2f)",
                        scaled_from, from_disp, scaled_to, to_disp, trade_usd)
            return None

        # Live execution
        if swap.chain == Chain.SOLANA:
            from execution.jupiter_executor import execute_jupiter
            wallet_pubkey = os.environ.get("WALLET_PUBLIC_KEY", "")
            rpc_url       = os.environ.get("SOLANA_RPC_URL", "")
            if not wallet_pubkey:
                logger.error("WALLET_PUBLIC_KEY env var not set — cannot execute")
                return None
            result = execute_jupiter(
                swap=swap,
                wallet_pubkey=wallet_pubkey,
                scaled_from_amount=scaled_from,
                slippage_bps=slippage_bps,
                mode="live",
                rpc_url=rpc_url,
            )
            if result.success:
                logger.info("[LIVE] Mirrored ~$%.2f: %s", trade_usd, result.tx_hash)
                return result.tx_hash
            else:
                logger.error("[LIVE] Mirror failed: %s", result.error)
                return None

        if swap.chain == Chain.BASE:
            logger.warning("Base execution not implemented yet")
            return None

        logger.warning("Unknown chain: %s", swap.chain)
        return None
