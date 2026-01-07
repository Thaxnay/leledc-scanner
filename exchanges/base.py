"""Base exchange interface for the LeveLeledc Scanner."""

from abc import ABC, abstractmethod
import pandas as pd


class BaseExchange(ABC):
    """Abstract base class for exchange adapters."""

    name: str = "base"
    quote_asset: str = "USD"  # Default quote asset
    rate_limit_delay: float = 0.1  # Delay between API calls

    @abstractmethod
    def get_all_pairs(self) -> list:
        """
        Fetch all trading pairs with the quote asset (e.g., USDT, USD).

        Returns:
            List of symbol strings (e.g., ['BTCUSDT', 'ETHUSDT', ...])
        """
        pass

    @abstractmethod
    def get_24h_volumes(self) -> dict:
        """
        Get 24-hour trading volume for all pairs.

        Returns:
            Dict mapping symbol -> volume in quote asset
            e.g., {'BTCUSDT': 1000000000.0, 'ETHUSDT': 500000000.0, ...}
        """
        pass

    @abstractmethod
    def get_klines(self, symbol: str, interval: str = '1w', limit: int = 100) -> pd.DataFrame:
        """
        Fetch OHLCV candlestick data for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT')
            interval: Timeframe ('1w' for weekly)
            limit: Number of candles to fetch

        Returns:
            DataFrame with columns: open_time, open, high, low, close, volume
            Returns None if data cannot be fetched.
        """
        pass

    def normalize_symbol(self, symbol: str) -> str:
        """
        Normalize a symbol to the exchange's format.
        Override in subclasses if needed.
        """
        return symbol

    def display_symbol(self, symbol: str) -> str:
        """
        Convert exchange symbol to display format.
        Override in subclasses if needed.
        """
        return symbol
