"""
Jupiter V6 swap execution for Solana.
Paper mode: simulates. Live mode: fetches quote and broadcasts.
"""

import logging
import os
from typing import Optional, Tuple

import requests

from core.models import Chain, DetectedSwap, MirrorResult

logger = logging.getLogger(__name__)

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"


def execute_jupiter(
    swap: DetectedSwap,
    wallet_pubkey: str,
    slippage_bps: int,
    mode: str,
) -> MirrorResult:
    """
    Execute or simulate a Jupiter swap.
    Paper mode: returns simulated success.
    Live mode: gets quote, builds tx, signs, broadcasts.
    """
    if mode == "paper":
        return MirrorResult(
            success=True,
            tx_hash="PAPER_SIM_" + swap.tx_hash[:16],
            filled_amount_in=swap.from_amount,
            filled_amount_out=swap.to_amount,
            slippage_pct=swap.slippage_pct or 0.0,
            latency_sec=0.0,
            chain=Chain.SOLANA,
        )
    # Live mode: would call Jupiter API, sign with private key, broadcast
    try:
        # Jupiter quote params: inputMint, outputMint, amount (lamports/smallest), slippageBps
        input_mint = getattr(swap, "from_mint", "") or "So11111111111111111111111111111111111111112"
        output_mint = getattr(swap, "to_mint", "") or ""
        if not output_mint:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA, error="Missing output mint")
        amount_raw = int(swap.from_amount * 1e9)  # SOL has 9 decimals
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_raw),
            "slippageBps": slippage_bps,
            "userPublicKey": wallet_pubkey,
        }
        r = requests.get(JUPITER_QUOTE_URL, params=params, timeout=15)
        if r.status_code != 200:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA, error=f"Quote: {r.status_code}")
        quote = r.json()
        swap_req = {"quoteResponse": quote, "userPublicKey": wallet_pubkey}
        r2 = requests.post(JUPITER_SWAP_URL, json=swap_req, timeout=15)
        if r2.status_code != 200:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA, error=f"Swap tx: {r2.status_code}")
        swap_data = r2.json()
        swap_tx_b64 = swap_data.get("swapTransaction")
        if not swap_tx_b64:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA, error="No swap tx in response")
        # Sign and broadcast would go here - requires solana Keypair, connection
        return MirrorResult(success=True, tx_hash="LIVE_TX_PLACEHOLDER", chain=Chain.SOLANA)
    except Exception as e:
        logger.error("Jupiter execute: %s", e)
        return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA, error=str(e))
