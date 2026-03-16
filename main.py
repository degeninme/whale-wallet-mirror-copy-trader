#!/usr/bin/env python3
"""
Whale Wallet Mirror Copy Trader — main entry point.
Surveillance → Decode → Score → Risk Gate → Mirror Engine.
"""

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from core.config_loader import load_config
from core.decoder import decode_swap
from core.models import Chain, DetectedSwap
from core.risk_gate import RiskGate
from core.scorer import WalletScorer
from execution.mirror_engine import MirrorEngine
from surveillance.base_monitor import BaseMonitor
from surveillance.solana_monitor import SolanaMonitor

PROJECT_ROOT = Path(__file__).resolve().parent


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Suppress noisy lib loggers
    for name in ("urllib3", "web3", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)


def run_demo(settings: dict, wallet_tiers: dict, log_level: str, is_paper: bool):
    """Run a single demo swap through the full pipeline (no live RPC)."""
    setup_logging(log_level)
    logger = logging.getLogger(__name__)

    slippage = settings.get("slippage", {})
    slippage_tiers = slippage
    max_pos = float(settings.get("wallet", {}).get("max_position_usd", 500))
    mirror_scale = float(settings.get("wallet", {}).get("mirror_scale", 0.08))
    min_score = int(settings.get("watchlist", {}).get("min_score_to_mirror", 65))

    scorer = WalletScorer(wallet_tiers, min_score)
    risk_gate = RiskGate(max_position_usd=max_pos, min_score=min_score, slippage_tiers=slippage_tiers)
    mirror_engine = MirrorEngine(mirror_scale=mirror_scale, is_paper=is_paper)

    # Mock swap (Solana-style) - amount under max_position_usd cap for demo
    swap = DetectedSwap(
        chain=Chain.SOLANA,
        wallet_address="7xKpm4E2nM7R3fEXAMPLE",
        tx_hash="3qVm9hJLEXAMPLE",
        from_token="SOL",
        from_amount=2.4,
        to_token="BONK (DezX...wFr9)",
        to_amount=4488022,
        from_amount_usd=400,
        to_amount_usd=400,
        dex="Jupiter Aggregator v6",
        timestamp=datetime.utcnow(),
        price_impact_pct=0.4,
    )
    swap = scorer.enrich_swap(swap)
    swap = decode_swap(swap)

    print("\n" + "═" * 70)
    print(" WHALE WALLET MIRROR — DEMO EXECUTION (Paper Mode)")
    print(" Chain: Solana Mainnet | Mode: DEMO | Session:", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    print("═" * 70)

    logger.info("🔍 MONITOR  Scanning wallets... (demo: 1 mock swap)")
    logger.info(
        "🚨 DETECTED Wallet: %s  |  Chain: SOLANA",
        swap.wallet_address[:8] + "..." + swap.wallet_address[-4:],
    )
    logger.info("               Action  : SWAP")
    logger.info("               From    : %.1f %s  (~$%s USD)", swap.from_amount, swap.from_token, int(swap.from_amount_usd or 0))
    to_display = swap.to_token[:24] + "..." if len(swap.to_token) > 24 else swap.to_token
    logger.info("               To      : %s", to_display)
    logger.info("               DEX     : %s", swap.dex)
    logger.info("               Slippage: 1.8%%  |  Price Impact: %.1f%%", swap.price_impact_pct)

    passed, reason = risk_gate.check(swap, position_usd_estimate=swap.from_amount_usd or 0)
    if not passed:
        logger.warning("⛔ REJECTED %s", reason)
        print("═" * 70)
        return

    slippage_bps = risk_gate.get_slippage_bps(swap.wallet_tier or "B")
    scaled_from = swap.from_amount * mirror_scale
    scaled_to = swap.to_amount * mirror_scale

    logger.info("⚙️  ENGINE  Risk check passed ✅  |  Position cap: $%s", int(max_pos))
    logger.info("               Sizing  : %.2fx mirror (scaled from source wallet)", mirror_scale)
    logger.info("               Tier    : %s", swap.wallet_tier)

    logger.info("🔄 SUBMIT   Mirror tx constructed → (paper: no broadcast)")
    tx_hash = mirror_engine.execute_mirror(swap, slippage_bps)
    if is_paper:
        tx_hash = "PAPER_" + swap.tx_hash[:12]

    to_display = swap.to_token[:16] + ".." if len(swap.to_token) > 16 else swap.to_token
    logger.info("✅ FILLED   Tx: %s  |  Filled: %.2f %s → %.0f %s", tx_hash or "PAPER", scaled_from, swap.from_token, scaled_to, to_display)
    logger.info("               Latency : 2.1s (simulated)")
    logger.info("               Status  : CONFIRMED (paper)")

    print("─" * 70)
    logger.info("📋 SUMMARY  Mirror #001 complete (demo)")
    logger.info("               Wallet rank  : %s", swap.wallet_tier)
    logger.info("               Paper mode   : No real execution")
    print("═" * 70)
    print("\nDemo complete. Run without --demo and with RPC endpoints for live surveillance.\n")


def main():
    parser = argparse.ArgumentParser(description="Whale Wallet Mirror Copy Trader")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="paper = simulate, live = execute")
    parser.add_argument("--chain", choices=["solana", "base", "all"], default="all", help="Chain(s) to monitor")
    parser.add_argument("--demo", action="store_true", help="Run single demo swap (no RPC)")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--config", default="config/settings.yaml", help="Settings path")
    args = parser.parse_args()

    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    config_path = PROJECT_ROOT / args.config
    if not config_path.exists():
        example = PROJECT_ROOT / "config" / "settings.example.yaml"
        if example.exists():
            import shutil
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(example, config_path)
            print("Created config from example. Edit config/settings.yaml and run again.")
        else:
            print("ERROR: config not found.")
            sys.exit(1)

    settings = load_config(str(config_path), "settings")
    wallets_path = PROJECT_ROOT / "config" / "wallets.yaml"
    wallet_cfg = load_config(str(wallets_path), "wallets")
    if not wallet_cfg:
        w_example = PROJECT_ROOT / "config" / "wallets.example.yaml"
        if w_example.exists():
            import shutil
            shutil.copy(w_example, wallets_path)
            print("Created config/wallets.yaml from example. Add wallet addresses.")
            wallet_cfg = load_config(str(wallets_path), "wallets")
        if not wallet_cfg:
            wallet_cfg = {}

    wallet_tiers = {}
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

    if not wallet_tiers and not args.demo:
        print("No wallets in config/wallets.yaml. Add addresses or run with --demo.")
        sys.exit(1)

    if args.demo:
        # Ensure demo mock wallet always gets tier A for consistent demo output
        demo_tiers = dict(wallet_tiers or {})
        demo_tiers["7xKpm4E2nM7R3fEXAMPLE"] = "A"
        run_demo(settings, demo_tiers, args.log_level, args.mode == "paper")
        return

    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    rpc = settings.get("rpc", {})
    solana_url = os.environ.get("SOLANA_RPC_URL") or rpc.get("solana", "")
    base_url = os.environ.get("BASE_RPC_URL") or rpc.get("base", "")

    solana_wallets = [a for a in wallet_tiers if len(a) >= 32 and not a.startswith("0x")]
    base_wallets = [a for a in wallet_tiers if a.startswith("0x")]

    slippage = settings.get("slippage", {})
    max_pos = float(settings.get("wallet", {}).get("max_position_usd", 500))
    mirror_scale = float(settings.get("wallet", {}).get("mirror_scale", 0.08))
    min_score = int(settings.get("watchlist", {}).get("min_score_to_mirror", 65))

    scorer = WalletScorer(wallet_tiers, min_score)
    risk_gate = RiskGate(max_position_usd=max_pos, min_score=min_score, slippage_tiers=slippage)
    mirror_engine = MirrorEngine(mirror_scale=mirror_scale, is_paper=(args.mode == "paper"))

    def on_swap(swap: DetectedSwap):
        swap = scorer.enrich_swap(swap)
        swap = decode_swap(swap)
        if not scorer.should_mirror(swap.wallet_address):
            logger.info("Skip mirror: wallet %s below min score", swap.wallet_address[:12])
            return
        pos_usd = swap.from_amount_usd or (swap.from_amount * 0.5)
        passed, reason = risk_gate.check(swap, pos_usd)
        if not passed:
            logger.warning("Risk gate: %s", reason)
            return
        slippage_bps = risk_gate.get_slippage_bps(swap.wallet_tier or "B")
        mirror_engine.execute_mirror(swap, slippage_bps)

    threads = []
    if args.chain in ("solana", "all") and solana_url and solana_wallets:
        sol_mon = SolanaMonitor(solana_url, solana_wallets, on_swap, poll_interval_sec=3.0)
        t = threading.Thread(target=sol_mon.run, daemon=True)
        t.start()
        threads.append(t)
        logger.info("Solana monitor started (%d wallets)", len(solana_wallets))

    if args.chain in ("base", "all") and base_url and base_wallets:
        base_mon = BaseMonitor(base_url, base_wallets, on_swap, poll_interval_sec=2.0)
        t = threading.Thread(target=base_mon.run, daemon=True)
        t.start()
        threads.append(t)
        logger.info("Base monitor started (%d wallets)", len(base_wallets))

    if not threads:
        print("No RPC URLs or wallets configured. Set SOLANA_RPC_URL, BASE_RPC_URL in .env and add wallets to config/wallets.yaml.")
        sys.exit(1)

    logger.info("Surveillance running (mode=%s). Ctrl+C to stop.", args.mode)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
