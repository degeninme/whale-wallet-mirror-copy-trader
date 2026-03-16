"""Load and validate configuration from YAML and environment."""

import os
from pathlib import Path
from typing import Any, Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load .env into os.environ."""
    try:
        from dotenv import load_dotenv
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


def load_yaml(path) -> dict:
    """Load YAML file. Return empty dict if missing."""
    p = Path(path) if not isinstance(path, Path) else path
    if not p.exists():
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def load_config(path: str, config_type: str = "settings") -> dict:
    """Load config. config_type: 'settings' or 'wallets'."""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if config_type == "settings":
        return load_settings(p)
    return load_yaml(p)


def load_settings(config_path: Optional[Path] = None) -> dict:
    """Load settings.yaml. Merge with env overrides."""
    _load_dotenv()
    path = config_path or PROJECT_ROOT / "config" / "settings.yaml"
    if not path.exists():
        example = PROJECT_ROOT / "config" / "settings.example.yaml"
        if example.exists():
            path = example
    settings = load_yaml(path)
    # Env overrides for RPC (always merge so env can override config)
    if "rpc" not in settings:
        settings["rpc"] = {}
    settings["rpc"]["solana"] = os.environ.get("SOLANA_RPC_URL") or settings["rpc"].get("solana", "")
    settings["rpc"]["base"] = os.environ.get("BASE_RPC_URL") or settings["rpc"].get("base", "")
    return settings


def load_wallets(config_path: Optional[Path] = None) -> dict:
    """Load wallets.yaml (watchlist)."""
    path = config_path or PROJECT_ROOT / "config" / "wallets.yaml"
    if not path.exists():
        path = PROJECT_ROOT / "config" / "wallets.example.yaml"
    return load_yaml(path)


def get_wallet_private_key() -> str:
    """Get execution wallet private key from env."""
    _load_dotenv()
    return os.environ.get("WALLET_PRIVATE_KEY", "").strip()
