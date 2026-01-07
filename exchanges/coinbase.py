"""Coinbase Exchange adapter."""

import requests
import pandas as pd
from datetime import datetime, timedelta
from .base import BaseExchange


class CoinbaseExchange(BaseExchange):
    """Coinbase Exchange (formerly Coinbase Pro/GDAX) adapter."""

    name = "coinbase"
    quote_asset = "USD"
    base_url = "https://api.exchange.coinbase.com"
    rate_limit_delay = 0.15  # Coinbase has stricter rate limits

    # Coinbase granularity is in seconds
    # Max supported: 86400 (1 day), so we fetch daily and resample to weekly
    GRANULARITY_MAP = {
        '1d': 86400,
        '1w': 86400,  # Fetch daily, resample to weekly
    }

    def get_all_pairs(self) -> list:
        """Fetch all USD trading pairs from Coinbase."""
        url = f"{self.base_url}/products"
        headers = {'Accept': 'application/json'}

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        usd_pairs = []
        for product in data:
            if (product.get('quote_currency') == 'USD' and
                product.get('status') == 'online' and
                not product.get('trading_disabled', False)):
                usd_pairs.append(product['id'])

        return usd_pairs

    def get_24h_volumes(self) -> dict:
        """Get 24h volume for all USD pairs."""
        # Coinbase requires fetching stats per product
        # First get all products, then batch fetch stats
        pairs = self.get_all_pairs()
        volumes = {}

        for pair in pairs:
            try:
                url = f"{self.base_url}/products/{pair}/stats"
                headers = {'Accept': 'application/json'}
                response = requests.get(url, headers=headers, timeout=10)

                if response.status_code == 200:
                    stats = response.json()
                    # Volume in quote currency (USD)
                    volume = float(stats.get('volume', 0)) * float(stats.get('last', 0))
                    volumes[pair] = volume
            except Exception:
                continue

        return volumes

    def get_klines(self, symbol: str, interval: str = '1w', limit: int = 100) -> pd.DataFrame:
        """
        Fetch OHLCV data from Coinbase.

        Note: Coinbase max granularity is 1 day, so for weekly we fetch
        daily candles and resample.
        """
        url = f"{self.base_url}/products/{symbol}/candles"
        headers = {'Accept': 'application/json'}

        # Calculate time range
        # For weekly data, we need more daily candles
        days_needed = limit * 7 if interval == '1w' else limit
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days_needed)

        params = {
            'granularity': self.GRANULARITY_MAP.get(interval, 86400),
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)

            if response.status_code != 200:
                return None

            data = response.json()

            if not data or not isinstance(data, list):
                return None

            # Coinbase returns: [time, low, high, open, close, volume]
            # Note: order is different from Binance!
            df = pd.DataFrame(data, columns=['open_time', 'low', 'high', 'open', 'close', 'volume'])

            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            df['open_time'] = pd.to_datetime(df['open_time'], unit='s')

            # Sort by time ascending (Coinbase returns descending)
            df = df.sort_values('open_time').reset_index(drop=True)

            # Resample to weekly if needed
            if interval == '1w':
                df = self._resample_to_weekly(df)

            return df

        except Exception:
            return None

    def _resample_to_weekly(self, df: pd.DataFrame) -> pd.DataFrame:
        """Resample daily data to weekly candles."""
        if df is None or len(df) < 7:
            return None

        df = df.set_index('open_time')

        resampled = df.resample('W').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        resampled = resampled.reset_index()
        return resampled

    def display_symbol(self, symbol: str) -> str:
        """Convert Coinbase symbol (BTC-USD) to display format."""
        return symbol.replace('-', '')
