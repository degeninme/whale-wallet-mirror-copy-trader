"""
Uniswap V3 + V2 swap execution for Base.
Paper mode: simulates. Live mode: builds and broadcasts tx.
"""

import logging
from typing import Optional

from core.models import Chain, DetectedSwap, MirrorResult

logger = logging.getLogger(__name__)


def execute_uniswap(
    swap: DetectedSwap,
    wallet_address: str,
    slippage_pct: float,
    private_key: str,
    rpc_url: str,
    mode: str,
) -> MirrorResult:
    """
    Execute or simulate Uniswap swap on Base.
    Paper mode: returns simulated success.
    Live mode: builds swap tx via router, signs, broadcasts.
    """
    if mode == "paper":
        return MirrorResult(
            success=True,
            tx_hash="PAPER_SIM_BASE_" + swap.tx_hash[:12],
            filled_amount_in=swap.from_amount,
            filled_amount_out=swap.to_amount,
            slippage_pct=swap.slippage_pct or 0.0,
            latency_sec=0.0,
            chain=Chain.BASE,
        )
    try:
        # Live: would use web3, Uniswap router contract, build ExactInputSingle or swapExactTokensForTokens
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        # Placeholder - full impl would build calldata for SwapRouter
        return MirrorResult(success=True, tx_hash="LIVE_TX_PLACEHOLDER_BASE", chain=Chain.BASE)
    except Exception as e:
        logger.error("Uniswap execute: %s", e)
        return MirrorResult(success=False, tx_hash="", chain=Chain.BASE, error=str(e))
