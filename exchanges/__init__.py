"""Exchange adapters for the LeveLeledc Scanner."""

from .base import BaseExchange
from .binance import BinanceExchange
from .coinbase import CoinbaseExchange
from .kraken import KrakenExchange

EXCHANGES = {
    'binance': BinanceExchange,
    'coinbase': CoinbaseExchange,
    'kraken': KrakenExchange,
}


def get_exchange(name: str) -> BaseExchange:
    """Get an exchange instance by name."""
    name = name.lower()
    if name not in EXCHANGES:
        available = ', '.join(EXCHANGES.keys())
        raise ValueError(f"Unknown exchange '{name}'. Available: {available}")
    return EXCHANGES[name]()


def list_exchanges() -> list:
    """Return list of supported exchange names."""
    return list(EXCHANGES.keys())
