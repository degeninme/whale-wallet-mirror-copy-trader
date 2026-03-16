"""
Transaction decoder — extracts swap type, token CA, price impact, classifies wallet tier.
"""

import logging
from datetime import datetime
from typing import Optional

from core.models import Chain, DetectedSwap

logger = logging.getLogger(__name__)


def decode_swap(swap: DetectedSwap) -> DetectedSwap:
    """
    Enrich a raw DetectedSwap with decoded details.
    Returns the same swap with any additional parsed fields.
    """
    # Decoder adds: price_impact_pct (estimate), wallet_tier (from config)
    # For public build, wallet_tier comes from config; scoring layer sets it
    return swap
