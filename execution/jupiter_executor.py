"""
Jupiter V6 swap execution for Solana.
Paper mode: simulates. Live mode: fetches quote, signs, and broadcasts.
"""

import base64
import logging
import os
from typing import Optional

import requests

from core.models import Chain, DetectedSwap, MirrorResult

logger = logging.getLogger(__name__)

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL  = "https://quote-api.jup.ag/v6/swap"
SOL_MINT          = "So11111111111111111111111111111111111111112"


def _get_keypair():
    """Load Solana keypair from WALLET_PRIVATE_KEY env var (base58 or JSON byte array)."""
    try:
        from solders.keypair import Keypair  # type: ignore
        raw = os.environ.get("WALLET_PRIVATE_KEY", "").strip()
        if not raw:
            raise ValueError("WALLET_PRIVATE_KEY not set")
        # Support both base58 string and JSON byte array "[1,2,3,...]"
        if raw.startswith("["):
            import json
            bts = bytes(json.loads(raw))
            return Keypair.from_bytes(bts)
        return Keypair.from_base58_string(raw)
    except ImportError:
        raise RuntimeError("solders not installed — run: pip install solders")


def execute_jupiter(
    swap: DetectedSwap,
    wallet_pubkey: str,
    scaled_from_amount: float,
    slippage_bps: int,
    mode: str,
    rpc_url: str = "",
) -> MirrorResult:
    """
    Execute or simulate a Jupiter swap.
    Paper mode: returns simulated success.
    Live mode: gets quote → builds tx → signs → broadcasts.
    """
    if mode == "paper":
        return MirrorResult(
            success=True,
            tx_hash="PAPER_SIM_" + swap.tx_hash[:16],
            filled_amount_in=scaled_from_amount,
            filled_amount_out=swap.to_amount,
            slippage_pct=swap.slippage_pct if hasattr(swap, "slippage_pct") else 0.0,
            latency_sec=0.0,
            chain=Chain.SOLANA,
        )

    try:
        from solders.transaction import VersionedTransaction  # type: ignore
        from solders.rpc.requests import SendVersionedTransaction  # type: ignore
        import httpx  # type: ignore

        input_mint  = getattr(swap, "from_mint", "") or SOL_MINT
        output_mint = getattr(swap, "to_mint", "")
        if not output_mint:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA,
                                error="Missing output mint address")

        # Convert to lamports (SOL = 9 decimals; tokens vary — use 6 as safe default)
        decimals   = 9 if input_mint == SOL_MINT else 6
        amount_raw = int(scaled_from_amount * (10 ** decimals))

        # 1. Get quote
        params = {
            "inputMint":    input_mint,
            "outputMint":   output_mint,
            "amount":       str(amount_raw),
            "slippageBps":  slippage_bps,
            "userPublicKey": wallet_pubkey,
        }
        r = requests.get(JUPITER_QUOTE_URL, params=params, timeout=15)
        if r.status_code != 200:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA,
                                error=f"Quote failed: {r.status_code} {r.text[:200]}")
        quote = r.json()

        # 2. Build swap transaction
        swap_req = {
            "quoteResponse":            quote,
            "userPublicKey":            wallet_pubkey,
            "wrapAndUnwrapSol":         True,
            "dynamicComputeUnitLimit":  True,
            "prioritizationFeeLamports": "auto",
        }
        r2 = requests.post(JUPITER_SWAP_URL, json=swap_req, timeout=15)
        if r2.status_code != 200:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA,
                                error=f"Swap build failed: {r2.status_code} {r2.text[:200]}")
        swap_data   = r2.json()
        swap_tx_b64 = swap_data.get("swapTransaction")
        if not swap_tx_b64:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA,
                                error="No swapTransaction in Jupiter response")

        # 3. Sign
        keypair    = _get_keypair()
        raw_tx     = base64.b64decode(swap_tx_b64)
        tx         = VersionedTransaction.from_bytes(raw_tx)
        signed_tx  = keypair.sign_message(bytes(tx.message))
        tx.signatures[0] = signed_tx
        signed_bytes = bytes(tx)

        # 4. Broadcast via RPC
        rpc = rpc_url or os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                base64.b64encode(signed_bytes).decode(),
                {"encoding": "base64", "skipPreflight": False,
                 "preflightCommitment": "confirmed", "maxRetries": 3},
            ],
        }
        r3 = requests.post(rpc, json=payload, timeout=20)
        result = r3.json()
        if "error" in result:
            return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA,
                                error=f"RPC error: {result['error']}")

        tx_hash = result.get("result", "")
        logger.info("Jupiter swap sent: %s", tx_hash)
        return MirrorResult(success=True, tx_hash=tx_hash, chain=Chain.SOLANA,
                            filled_amount_in=scaled_from_amount)

    except Exception as e:
        logger.error("Jupiter execute: %s", e)
        return MirrorResult(success=False, tx_hash="", chain=Chain.SOLANA, error=str(e))
