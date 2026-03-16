"""Load and validate configuration from YAML and environment."""

import os
from pathlib import Path
from typing import Any, Optional, Union

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Cache TTL: config files are typically static during a session
_CONFIG_CACHE: dict = {}
_CACHE_ENABLED = True


def _load_dotenv() -> None:
    """Load .env into os.environ."""
    try:
        from dotenv import load_dotenv
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


def load_yaml(path: Union[str, Path]) -> dict:
    """Load YAML file. Return empty dict if missing. Uses minimal I/O."""
    p = Path(path) if not isinstance(path, Path) else path
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str, config_type: str = "settings", use_cache: bool = True) -> dict:
    """Load config. config_type: 'settings' or 'wallets'. Cached for session."""
    p = Path(path)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    cache_key = (str(p), config_type)
    if use_cache and _CACHE_ENABLED and cache_key in _CONFIG_CACHE:
        return _CONFIG_CACHE[cache_key]
    result = load_settings(p) if config_type == "settings" else load_yaml(p)
    if use_cache and _CACHE_ENABLED:
        _CONFIG_CACHE[cache_key] = result
    return result


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
