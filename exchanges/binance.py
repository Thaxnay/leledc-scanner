"""Binance exchange adapter."""

import requests
import pandas as pd
from .base import BaseExchange


class BinanceExchange(BaseExchange):
    """Binance exchange adapter with Binance.US fallback."""

    name = "binance"
    quote_asset = "USDT"

    # Try main Binance first, fall back to Binance.US for geo-restricted regions
    base_urls = [
        "https://api.binance.com/api/v3",
        "https://api.binance.us/api/v3"
    ]

    def __init__(self):
        super().__init__()
        self.base_url = None  # Will be set on first successful request

    def _request(self, endpoint: str, params: dict = None, timeout: int = 30):
        """Make request with automatic fallback to Binance.US."""
        urls_to_try = [self.base_url] if self.base_url else self.base_urls

        for base_url in urls_to_try:
            try:
                url = f"{base_url}{endpoint}"
                response = requests.get(url, params=params, timeout=timeout)

                # 451 = geo-restricted, try next URL
                if response.status_code == 451:
                    continue

                response.raise_for_status()
                self.base_url = base_url  # Remember working URL
                return response.json()

            except requests.exceptions.RequestException:
                continue

        raise Exception("Binance API unavailable (geo-restricted). Try Coinbase or Kraken.")

    def get_all_pairs(self) -> list:
        """Fetch all USDT trading pairs from Binance."""
        data = self._request("/exchangeInfo")

        usdt_pairs = []
        for symbol in data['symbols']:
            if (symbol['quoteAsset'] == 'USDT' and
                symbol['status'] == 'TRADING' and
                symbol['isSpotTradingAllowed']):
                usdt_pairs.append(symbol['symbol'])

        return usdt_pairs

    def get_24h_volumes(self) -> dict:
        """Get 24h volume for all pairs."""
        data = self._request("/ticker/24hr")

        volumes = {}
        for ticker in data:
            if ticker['symbol'].endswith('USDT'):
                volumes[ticker['symbol']] = float(ticker['quoteVolume'])

        return volumes

    def get_klines(self, symbol: str, interval: str = '1w', limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV data from Binance."""
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }

        try:
            data = self._request("/klines", params=params, timeout=10)

            if isinstance(data, dict) and 'code' in data:
                return None

            df = pd.DataFrame(data, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])

            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')

            return df

        except Exception:
            return None
