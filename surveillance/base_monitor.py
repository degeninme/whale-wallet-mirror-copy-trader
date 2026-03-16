"""
Base chain wallet surveillance via Web3 block polling.
Detects Uniswap V2/V3 swap events from watched wallets.
Optimized: frozenset topic lookup O(1), single Web3 instance reuse.
"""

import logging
import time
from datetime import datetime
from typing import Callable, List, Optional, Set

from web3 import Web3
from web3.types import BlockData, TxReceipt

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)

# Uniswap V2/V3 Swap event topics — frozenset for O(1) membership test
UNISWAP_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
UNISWAP_V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
SWAP_TOPICS = frozenset((UNISWAP_V2_SWAP.lower(), UNISWAP_V3_SWAP.lower()))

# Common Base DEXes (factory / router addresses can be used to find pools)
# For logs we subscribe to specific pool addresses or use broader filter
# Simplified: we poll blocks and check tx from addresses


class BaseMonitor:
    """Monitor Base chain wallets for Uniswap V2/V3 swap activity."""

    def __init__(
        self,
        rpc_url: str,
        watchlist: List[str],
        on_swap: Callable[[DetectedSwap], None],
        poll_interval_sec: float = 2.0,
    ):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        wl = set()
        for a in watchlist:
            if not a or not a.strip():
                continue
            try:
                wl.add(self.w3.to_checksum_address(a.strip()))
            except Exception:
                wl.add(a.strip().lower())
        self.watchlist = wl
        self.on_swap = on_swap
        self.poll_interval = poll_interval_sec
        self._last_block: int = 0
        self._seen_tx: Set[str] = set()
        self._running = False

    def _parse_swap_logs(self, wallet: str, tx_hash: str, receipt: TxReceipt) -> Optional[DetectedSwap]:
        """Parse receipt logs for Uniswap V2/V3 Swap events."""
        try:
            for log in receipt.get("logs") or []:
                topics = log.get("topics") or []
                if len(topics) < 1:
                    continue
                t0 = (topics[0] or b"").hex() if isinstance(topics[0], bytes) else str(topics[0])
                if t0.lower() not in SWAP_TOPICS:
                    continue
                data = log.get("data", "0x")
                if isinstance(data, bytes):
                    data = "0x" + data.hex()
                if len(data) < 138:  # 2*32 + 2*32 + address
                    continue
                # V2: amount0In, amount1In, amount0Out, amount1Out
                # We use a simplified extraction - just flag as swap
                amount0_in = int(data[2:66], 16) if len(data) >= 66 else 0
                amount1_in = int(data[66:130], 16) if len(data) >= 130 else 0
                amount0_out = int(data[130:194], 16) if len(data) >= 194 else 0
                amount1_out = int(data[194:258], 16) if len(data) >= 258 else 0
                # Approximate: one side in, other out
                from_amt = max(amount0_in, amount1_in) / 1e18
                to_amt = max(amount0_out, amount1_out) / 1e18
                if from_amt < 1e-10 and to_amt < 1e-10:
                    from_amt = 1.0
                    to_amt = 1.0
                return DetectedSwap(
                    chain=Chain.BASE,
                    wallet_address=wallet,
                    tx_hash=tx_hash,
                    from_token="unknown",
                    from_amount=from_amt,
                    to_token="unknown",
                    to_amount=to_amt,
                    dex="Uniswap V3",
                    timestamp=datetime.utcnow(),
                )
        except Exception as e:
            logger.debug("Parse Base swap %s: %s", tx_hash[:16], e)
        return None

    def _poll_once(self):
        """Poll new blocks and check transactions from watched wallets."""
        try:
            block_num = self.w3.eth.block_number
            if self._last_block == 0:
                self._last_block = block_num - 1
            for bn in range(self._last_block + 1, block_num + 1):
                block = self.w3.eth.get_block(bn, full_transactions=True)
                txs = block.get("transactions") or []
                for tx in txs:
                    if isinstance(tx, dict):
                        fr = tx.get("from")
                    else:
                        fr = getattr(tx, "from_", None) or getattr(tx, "from", None)
                    if not fr:
                        continue
                    try:
                        addr = self.w3.to_checksum_address(fr)
                    except Exception:
                        continue
                    if addr not in self.watchlist:
                        continue
                    tx_hash = tx.get("hash") if isinstance(tx, dict) else tx.hash
                    tx_hash_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
                    if tx_hash_hex in self._seen_tx:
                        continue
                    self._seen_tx.add(tx_hash_hex)
                    try:
                        receipt = self.w3.eth.get_transaction_receipt(tx_hash)
                        if receipt and receipt.get("status") == 1:
                            swap = self._parse_swap_logs(addr, tx_hash_hex, receipt)
                            if swap:
                                self.on_swap(swap)
                    except Exception as e:
                        logger.debug("Receipt %s: %s", tx_hash_hex[:16], e)
            self._last_block = block_num
        except Exception as e:
            logger.warning("Base poll: %s", e)

    def run(self):
        """Run surveillance loop (blocking)."""
        self._running = True
        try:
            self._last_block = self.w3.eth.block_number - 1
        except Exception:
            self._last_block = 0
        while self._running:
            self._poll_once()
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
