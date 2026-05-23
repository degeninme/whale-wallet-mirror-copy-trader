"""
Solana wallet surveillance via RPC.
Monitors watched addresses for swap transactions (Jupiter, etc.).
Handles both native SOL <-> token and token <-> token swaps.
"""

import logging
import time
from typing import Callable, Dict, List, Optional, Set

import requests

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)

_RPC_PAYLOAD = {"jsonrpc": "2.0", "id": 1}
SOL_MINT     = "So11111111111111111111111111111111111111112"
LAMPORTS     = 1_000_000_000  # 1 SOL


class SolanaMonitor:
    """Monitor Solana wallets for swap activity via RPC polling."""

    def __init__(
        self,
        rpc_url: str,
        watchlist: List[str],
        on_swap: Callable[[DetectedSwap], None],
        poll_interval_sec: float = 3.0,
    ):
        self.rpc_url       = rpc_url
        self.watchlist     = [a.strip() for a in watchlist if a.strip()]
        self.on_swap       = on_swap
        self.poll_interval = poll_interval_sec
        self._last_sigs: Dict[str, Optional[str]] = {}
        self._seen: Set[str] = set()
        self._running = False
        self._session = requests.Session()

    def _rpc(self, method: str, params: list) -> Optional[dict]:
        try:
            payload = {**_RPC_PAYLOAD, "method": method, "params": params}
            r = self._session.post(self.rpc_url, json=payload, timeout=15)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                logger.warning("RPC error [%s]: %s", method, data["error"])
                return None
            return data.get("result")
        except Exception as e:
            logger.warning("RPC %s: %s", method, e)
            return None

    def _init_sigs(self):
        for addr in self.watchlist:
            try:
                result = self._rpc("getSignaturesForAddress", [addr, {"limit": 1}])
                if result and isinstance(result, list):
                    self._last_sigs[addr] = result[0].get("signature")
                else:
                    self._last_sigs[addr] = None
            except Exception as e:
                logger.warning("Init sig %s: %s", addr[:12], e)
                self._last_sigs[addr] = None

    def _parse_tx_for_swap(self, wallet: str, sig: str, tx: dict, meta: Optional[dict]) -> Optional[DetectedSwap]:
        """Parse Solana tx+meta into DetectedSwap. Handles SOL<->token and token<->token."""
        try:
            if not tx or not meta:
                return None

            pre_balances  = meta.get("preBalances") or []
            post_balances = meta.get("postBalances") or []
            pre_token     = meta.get("preTokenBalances") or []
            post_token    = meta.get("postTokenBalances") or []

            # Find the wallet's account index
            account_keys = []
            msg = tx.get("transaction", {}).get("message", {})
            raw_keys = msg.get("accountKeys") or []
            for k in raw_keys:
                if isinstance(k, dict):
                    account_keys.append(k.get("pubkey", ""))
                else:
                    account_keys.append(str(k))

            wallet_idx = None
            for i, k in enumerate(account_keys):
                if k == wallet:
                    wallet_idx = i
                    break

            # ── Token balance changes ────────────────────────────────────────
            pre_by_mint  = {}
            post_by_mint = {}
            for b in pre_token:
                mint = b.get("mint")
                amt  = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                if mint:
                    pre_by_mint[mint] = pre_by_mint.get(mint, 0) + amt
            for b in post_token:
                mint = b.get("mint")
                amt  = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                if mint:
                    post_by_mint[mint] = post_by_mint.get(mint, 0) + amt

            all_mints = set(pre_by_mint) | set(post_by_mint)
            token_in_mint, token_in_amt   = None, 0.0
            token_out_mint, token_out_amt = None, 0.0

            for mint in all_mints:
                diff = post_by_mint.get(mint, 0) - pre_by_mint.get(mint, 0)
                if diff < -0.0001:
                    token_in_mint, token_in_amt = mint, abs(diff)
                elif diff > 0.0001:
                    token_out_mint, token_out_amt = mint, diff

            # ── Native SOL change for wallet ─────────────────────────────────
            sol_diff = 0.0
            if wallet_idx is not None and wallet_idx < len(pre_balances) and wallet_idx < len(post_balances):
                sol_diff = (post_balances[wallet_idx] - pre_balances[wallet_idx]) / LAMPORTS

            # ── Determine swap direction ─────────────────────────────────────
            from_mint, from_amt = None, 0.0
            to_mint,   to_amt   = None, 0.0

            if token_in_mint and token_out_mint:
                # token → token
                from_mint, from_amt = token_in_mint, token_in_amt
                to_mint,   to_amt   = token_out_mint, token_out_amt
            elif token_in_mint and sol_diff > 0.001:
                # token → SOL
                from_mint, from_amt = token_in_mint, token_in_amt
                to_mint,   to_amt   = SOL_MINT, sol_diff
            elif token_out_mint and sol_diff < -0.001:
                # SOL → token (most common Jupiter swap)
                from_mint, from_amt = SOL_MINT, abs(sol_diff)
                to_mint,   to_amt   = token_out_mint, token_out_amt
            else:
                return None  # not a recognisable swap

            # Skip plain SOL transfers (both sides SOL = not a swap)
            if from_mint == SOL_MINT and to_mint == SOL_MINT:
                return None

            # USD estimate (1 SOL ≈ $150; replace with price feed for accuracy)
            SOL_PRICE_USD = 150.0
            from_usd = (from_amt * SOL_PRICE_USD) if from_mint == SOL_MINT else \
                       (to_amt * SOL_PRICE_USD if to_mint == SOL_MINT else 0.0)

            def _label(mint):
                if not mint or mint == SOL_MINT: return "SOL"
                return mint[:8] + "..." if len(mint) > 12 else mint

            from_label = _label(from_mint)
            to_label   = _label(to_mint)

            return DetectedSwap(
                chain=Chain.SOLANA,
                wallet_address=wallet,
                tx_hash=sig,
                from_token=from_label,
                from_amount=from_amt,
                from_amount_usd=from_usd,
                to_token=to_label,
                to_amount=to_amt,
                dex="Jupiter",
                price_impact_pct=0.0,
            )

        except Exception as e:
            logger.debug("Parse swap %s: %s", sig[:16], e)
        return None

    def _poll_once(self):
        for addr in self.watchlist:
            try:
                result = self._rpc(
                    "getSignaturesForAddress",
                    [addr, {"limit": 10, "commitment": "confirmed"}],
                )
                if not result or not isinstance(result, list):
                    continue

                last = self._last_sigs.get(addr)
                new_sigs = []
                for item in result:
                    sig = item.get("signature")
                    if not sig or sig == last or sig in self._seen:
                        break
                    new_sigs.append(sig)

                # Process oldest first
                for sig in reversed(new_sigs):
                    self._seen.add(sig)
                    tx_result = self._rpc(
                        "getTransaction",
                        [sig, {"encoding": "jsonParsed",
                               "maxSupportedTransactionVersion": 0,
                               "commitment": "confirmed"}],
                    )
                    if tx_result:
                        swap = self._parse_tx_for_swap(addr, sig, tx_result, tx_result.get("meta"))
                        if swap:
                            logger.info("Swap detected: %s %s→%s (%.4f)",
                                        addr[:12], swap.from_token, swap.to_token, swap.from_amount)
                            self.on_swap(swap)

                if result:
                    self._last_sigs[addr] = result[0].get("signature")

            except Exception as e:
                logger.warning("Poll %s: %s", addr[:12], e)

    def run(self):
        self._running = True
        self._init_sigs()
        logger.info("Solana monitor polling %d wallets every %.0fs", len(self.watchlist), self.poll_interval)
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.error("Poll loop: %s", e)
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
