"""Data models for swap detection and mirror execution."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class Chain(Enum):
    SOLANA = "solana"
    BASE = "base"


class WalletTier(Enum):
    S = "S"   # Top tier
    A = "A"
    B = "B"
    WATCH = "Watch"  # Monitor only, no mirror


@dataclass(slots=True)
class DetectedSwap:
    """A swap detected from a watched wallet. slots=True reduces memory footprint."""
    chain: Chain
    wallet_address: str
    tx_hash: str
    block_slot: Optional[int] = None
    block_number: Optional[int] = None
    from_token: str = ""
    from_amount: float = 0.0
    to_token: str = ""
    to_amount: float = 0.0
    from_amount_usd: float = 0.0
    to_amount_usd: float = 0.0
    dex: str = ""
    timestamp: Optional[datetime] = None
    price_impact_pct: float = 0.0
    wallet_tier: str = "B"
    wallet_score: Optional[int] = None


@dataclass
class MirrorDecision:
    """Risk gate output: approve or reject mirror."""
    approved: bool
    reason: str = ""
    scaled_from_amount: float = 0.0
    scaled_to_amount: float = 0.0
    slippage_bps: int = 0


@dataclass
class MirrorResult:
    """Result of a mirror execution (paper or live)."""
    success: bool
    tx_hash: str = ""
    chain: Chain = Chain.SOLANA
    wallet: str = ""
    from_amount: float = 0.0
    to_amount: float = 0.0
    from_token: str = ""
    to_token: str = ""
    latency_seconds: float = 0.0
    slippage_actual_pct: float = 0.0
    paper_mode: bool = True
    timestamp: Optional[datetime] = None
    error: str = ""


@dataclass
class WalletScore:
    """Wallet quality score (0-100)."""
    address: str
    chain: Chain
    score: int
    tier: WalletTier
    win_rate: float = 0.0
    trades_tracked: int = 0
    pnl_usd: float = 0.0
    last_trade_at: Optional[datetime] = None
