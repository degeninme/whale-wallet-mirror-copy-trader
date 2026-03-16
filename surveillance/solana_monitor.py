"""
Solana wallet surveillance via RPC.
Monitors watched addresses for swap transactions (Jupiter, etc.).
Optimized: persistent Session (connection pooling), minimal allocations in hot loop.
"""

import logging
import time
from typing import Callable, Dict, List, Optional, Set

import requests

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)

# RPC payload template — reuse to reduce allocation
_RPC_PAYLOAD = {"jsonrpc": "2.0", "id": 1}
GET_TX_OPTS = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}


class SolanaMonitor:
    """Monitor Solana wallets for swap activity via RPC polling."""

    def __init__(
        self,
        rpc_url: str,
        watchlist: List[str],
        on_swap: Callable[[DetectedSwap], None],
        poll_interval_sec: float = 3.0,
    ):
        self.rpc_url = rpc_url
        self.watchlist = [a.strip() for a in watchlist if a.strip()]
        self.on_swap = on_swap
        self.poll_interval = poll_interval_sec
        self._last_sigs: Dict[str, Optional[str]] = {}
        self._seen: Set[str] = set()
        self._running = False
        self._session = requests.Session()

    def _rpc(self, method: str, params: list) -> Optional[dict]:
        """Execute JSON-RPC call. Reuses session for connection pooling."""
        try:
            payload = {**_RPC_PAYLOAD, "method": method, "params": params}
            r = self._session.post(self.rpc_url, json=payload, timeout=15)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                logger.warning("RPC error: %s", data["error"])
                return None
            return data.get("result")
        except Exception as e:
            logger.warning("Solana RPC %s: %s", method, e)
            return None

    def _init_sigs(self):
        """Initialize last-seen signature per wallet."""
        for addr in self.watchlist:
            try:
                result = self._rpc("getSignaturesForAddress", [addr, {"limit": 1}])
                if result and isinstance(result, list) and len(result) > 0:
                    sig = result[0].get("signature")
                    if sig:
                        self._last_sigs[addr] = sig
                else:
                    self._last_sigs[addr] = None
            except Exception as e:
                logger.warning("Init sig %s: %s", addr[:12], e)
                self._last_sigs[addr] = None

    def _parse_tx_for_swap(
        self, wallet: str, sig: str, tx: dict, meta: Optional[dict]
    ) -> Optional[DetectedSwap]:
        """Parse Solana tx+meta into DetectedSwap if it looks like a swap."""
        try:
            if not tx or "transaction" not in tx:
                return None
            msg = tx.get("transaction", {}).get("message", {})
            if not msg:
                return None
            # Token balance changes indicate swap
            pre = (meta or {}).get("preTokenBalances") or []
            post = (meta or {}).get("postTokenBalances") or []
            if not pre or not post:
                return None
            from_amt, from_mint = 0.0, ""
            to_amt, to_mint = 0.0, ""
            pre_by_mint = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in pre}
            post_by_mint = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in post}
            all_mints = set(pre_by_mint.keys()) | set(post_by_mint.keys())
            for mint in all_mints:
                p = pre_by_mint.get(mint, 0)
                q = post_by_mint.get(mint, 0)
                diff = q - p
                if diff < -0.0001:
                    from_amt = abs(diff)
                    from_mint = mint or "unknown"
                elif diff > 0.0001:
                    to_amt = diff
                    to_mint = mint or "unknown"
            if from_amt <= 0 and to_amt <= 0:
                return None
            return DetectedSwap(
                chain=Chain.SOLANA,
                wallet_address=wallet,
                tx_hash=sig,
                from_token=(from_mint[:8] + "..." if len(from_mint) > 12 else from_mint),
                from_amount=from_amt,
                to_token=(to_mint[:8] + "..." if len(to_mint) > 12 else to_mint),
                to_amount=to_amt,
                dex="Jupiter",
                timestamp=datetime.utcnow(),
            )
        except Exception as e:
            logger.debug("Parse swap %s: %s", sig[:16], e)
        return None

    def _poll_once(self):
        """Poll for new signatures and invoke on_swap for detected swaps."""
        for addr in self.watchlist:
            try:
                result = self._rpc(
                    "getSignaturesForAddress",
                    [addr, {"limit": 15, "commitment": "confirmed"}],
                )
                if not result or not isinstance(result, list):
                    continue
                last = self._last_sigs.get(addr)
                for item in result:
                    sig = item.get("signature")
                    if not sig:
                        continue
                    if last and sig == last:
                        break
                    if sig in self._seen:
                        continue
                    self._seen.add(sig)
                    # Fetch full tx
                    tx_result = self._rpc(
                        "getTransaction",
                        [
                            sig,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            },
                        ],
                    )
                    if tx_result:
                        swap = self._parse_tx_for_swap(
                            addr,
                            sig,
                            tx_result,
                            tx_result.get("meta"),
                        )
                        if swap:
                            self.on_swap(swap)
                if result:
                    self._last_sigs[addr] = result[0].get("signature")
            except Exception as e:
                logger.warning("Poll %s: %s", addr[:12], e)

    def run(self):
        """Run surveillance loop (blocking)."""
        self._running = True
        self._init_sigs()
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.error("Poll loop: %s", e)
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
