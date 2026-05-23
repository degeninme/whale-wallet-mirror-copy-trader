"""
Dashboard backend — FastAPI server.
Bridges the bot's swap pipeline to the web dashboard via REST + WebSocket.
"""

import asyncio
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.config_loader import load_config
from core.decoder import decode_swap
from core.models import Chain, DetectedSwap
from core.risk_gate import RiskGate
from core.scorer import WalletScorer
from execution.mirror_engine import MirrorEngine

PROJECT_ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── In-memory state ──────────────────────────────────────────────────────────

MAX_FEED = 100          # keep last N swaps in memory
swap_feed: deque = deque(maxlen=MAX_FEED)
hourly_counts: dict = {}   # "HH:00" -> count of mirrors

stats = {
    "detected": 0,
    "mirrored": 0,
    "rejected": 0,
    "watching": 0,
}

connected_clients: list[WebSocket] = []

# ── Bot components (initialised in lifespan) ─────────────────────────────────

scorer: Optional[WalletScorer] = None
risk_gate: Optional[RiskGate] = None
mirror_engine: Optional[MirrorEngine] = None
wallet_tiers: dict = {}
settings: dict = {}

# ── Helpers ──────────────────────────────────────────────────────────────────

def swap_to_dict(swap: DetectedSwap, status: str, reason: str = "") -> dict:
    return {
        "ts": datetime.utcnow().isoformat() + "Z",
        "chain": swap.chain.value,
        "wallet": swap.wallet_address,
        "wallet_short": swap.wallet_address[:8] + "..." + swap.wallet_address[-4:],
        "tx_hash": swap.tx_hash,
        "from_token": swap.from_token,
        "from_amount": round(swap.from_amount, 4),
        "to_token": swap.to_token,
        "to_amount": round(swap.to_amount, 2),
        "from_amount_usd": round(swap.from_amount_usd or 0, 2),
        "dex": swap.dex,
        "price_impact_pct": round(swap.price_impact_pct or 0, 2),
        "tier": swap.wallet_tier or "B",
        "score": swap.wallet_score or 0,
        "status": status,
        "reason": reason,
    }


async def broadcast(message: dict):
    """Send JSON to all connected WebSocket clients."""
    dead = []
    text = json.dumps(message)
    for ws in connected_clients:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.remove(ws)


def record_mirror_hour():
    key = datetime.utcnow().strftime("%H:00")
    hourly_counts[key] = hourly_counts.get(key, 0) + 1


# ── Swap callback (called from bot threads) ──────────────────────────────────

def on_swap(swap: DetectedSwap):
    global scorer, risk_gate, mirror_engine

    swap = scorer.enrich_swap(swap)
    swap = decode_swap(swap)

    stats["detected"] += 1

    if not scorer.should_mirror(swap.wallet_address):
        status, reason = "watching", "Score below threshold"
        stats["watching"] += 1
    else:
        pos_usd = swap.from_amount_usd or (swap.from_amount * 0.5)
        passed, fail_reason = risk_gate.check(swap, pos_usd)
        if not passed:
            status, reason = "rejected", fail_reason or "Risk gate"
            stats["rejected"] += 1
        else:
            slippage_bps = risk_gate.get_slippage_bps(swap.wallet_tier or "B")
            mirror_engine.execute_mirror(swap, slippage_bps)
            status, reason = "mirrored", ""
            stats["mirrored"] += 1
            record_mirror_hour()

    entry = swap_to_dict(swap, status, reason)
    swap_feed.appendleft(entry)

    # Push to dashboard — schedule coroutine from sync thread
    try:
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(
            broadcast({"type": "swap", "data": entry}),
            loop,
        )
    except RuntimeError:
        pass

    logger.info("[%s] %s %s → %s  status=%s", swap.chain.value, swap.wallet_address[:12],
                swap.from_token, swap.to_token, status)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Whale Mirror Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    global scorer, risk_gate, mirror_engine, wallet_tiers, settings

    config_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not config_path.exists():
        config_path = PROJECT_ROOT / "config" / "settings.example.yaml"

    settings = load_config(str(config_path), "settings")

    wallets_path = PROJECT_ROOT / "config" / "wallets.yaml"
    if not wallets_path.exists():
        wallets_path = PROJECT_ROOT / "config" / "wallets.example.yaml"
    wallet_cfg = load_config(str(wallets_path), "wallets") or {}

    _tier_map = {"tier_s": "S", "tier_a": "A", "tier_b": "B", "watch": "WATCH"}
    for chain_key in ("solana", "base"):
        chain_data = wallet_cfg.get(chain_key, {})
        if isinstance(chain_data, dict):
            for tier_key, addrs in chain_data.items():
                tier = _tier_map.get(tier_key.lower(), "B")
                if isinstance(addrs, list):
                    for a in addrs:
                        if isinstance(a, str) and len(a) > 10:
                            wallet_tiers[a] = tier
                elif isinstance(addrs, str) and len(addrs) > 10:
                    wallet_tiers[addrs] = tier

    wallet_cfg_inner = settings.get("wallet", {})
    slippage_tiers = settings.get("slippage", {}) or {}
    max_pos = float(wallet_cfg_inner.get("max_position_usd", 500))
    mirror_scale = float(wallet_cfg_inner.get("mirror_scale", 0.08))
    min_score = int(settings.get("watchlist", {}).get("min_score_to_mirror", 65))
    mode = os.environ.get("BOT_MODE") or settings.get("mode", "paper")

    scorer = WalletScorer(wallet_tiers, min_score)
    risk_gate = RiskGate(max_position_usd=max_pos, min_score=min_score, slippage_tiers=slippage_tiers)
    fixed_trade_usd = float(wallet_cfg_inner.get("fixed_trade_usd", 0)) or None
    mirror_engine = MirrorEngine(mirror_scale=mirror_scale, is_paper=(mode == "paper"), fixed_trade_usd=fixed_trade_usd)

    # Start bot monitors in background threads if RPC URLs present
    rpc = settings.get("rpc", {})
    solana_url = os.environ.get("SOLANA_RPC_URL") or rpc.get("solana", "")
    base_url = os.environ.get("BASE_RPC_URL") or rpc.get("base", "")

    solana_wallets = [a for a in wallet_tiers if len(a) >= 32 and not a.startswith("0x")]
    base_wallets = [a for a in wallet_tiers if a.startswith("0x")]

    if solana_url and solana_wallets:
        from surveillance.solana_monitor import SolanaMonitor
        mon = SolanaMonitor(solana_url, solana_wallets, on_swap, poll_interval_sec=3.0)
        t = threading.Thread(target=mon.run, daemon=True, name="solana-monitor")
        t.start()
        logger.info("Solana monitor started (%d wallets)", len(solana_wallets))

    if base_url and base_wallets:
        from surveillance.base_monitor import BaseMonitor
        mon = BaseMonitor(base_url, base_wallets, on_swap, poll_interval_sec=2.0)
        t = threading.Thread(target=mon.run, daemon=True, name="base-monitor")
        t.start()
        logger.info("Base monitor started (%d wallets)", len(base_wallets))

    logger.info("Dashboard API ready.")


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    return stats


@app.get("/api/swaps")
def get_swaps(limit: int = 50, chain: str = "all", status: str = "all"):
    result = list(swap_feed)
    if chain != "all":
        result = [s for s in result if s["chain"] == chain]
    if status != "all":
        result = [s for s in result if s["status"] == status]
    return result[:limit]


@app.get("/api/wallets")
def get_wallets():
    rows = []
    for addr, tier in wallet_tiers.items():
        score = {"S": 95, "A": 85, "B": 70, "WATCH": 50}.get(tier, 65)
        chain = "base" if addr.startswith("0x") else "solana"
        rows.append({"address": addr, "short": addr[:8] + "..." + addr[-4:], "tier": tier, "score": score, "chain": chain})
    return rows


@app.get("/api/config")
def get_config():
    wallet_cfg = settings.get("wallet", {})
    slippage = settings.get("slippage", {}) or {}
    return {
        "mode": settings.get("mode", "paper"),
        "max_position_usd": wallet_cfg.get("max_position_usd", 500),
        "mirror_scale": wallet_cfg.get("mirror_scale", 0.08),
        "min_score": settings.get("watchlist", {}).get("min_score_to_mirror", 65),
        "slippage": slippage,
        "poll_solana_sec": 3,
        "poll_base_sec": 2,
    }


@app.get("/api/activity")
def get_activity():
    """Last 12 hours of mirror counts."""
    now = datetime.utcnow()
    result = []
    for i in range(11, -1, -1):
        from datetime import timedelta
        h = (now - timedelta(hours=i)).strftime("%H:00")
        result.append({"hour": h, "count": hourly_counts.get(h, 0)})
    return result


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    logger.info("Dashboard client connected (total: %d)", len(connected_clients))
    try:
        # Send current state immediately on connect
        await websocket.send_text(json.dumps({
            "type": "init",
            "stats": stats,
            "swaps": list(swap_feed)[:50],
            "wallets": get_wallets(),
            "config": get_config(),
            "activity": get_activity(),
        }))
        while True:
            await websocket.receive_text()   # keep-alive; client can ping
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        logger.info("Dashboard client disconnected (total: %d)", len(connected_clients))


# ── Serve dashboard static files ─────────────────────────────────────────────

if DASHBOARD_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
