"""Binance exchange adapter."""

import requests
import pandas as pd
from .base import BaseExchange


class BinanceExchange(BaseExchange):
    """Binance exchange adapter."""

    name = "binance"
    quote_asset = "USDT"
    base_url = "https://api.binance.com/api/v3"

    def get_all_pairs(self) -> list:
        """Fetch all USDT trading pairs from Binance."""
        url = f"{self.base_url}/exchangeInfo"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        usdt_pairs = []
        for symbol in data['symbols']:
            if (symbol['quoteAsset'] == 'USDT' and
                symbol['status'] == 'TRADING' and
                symbol['isSpotTradingAllowed']):
                usdt_pairs.append(symbol['symbol'])

        return usdt_pairs

    def get_24h_volumes(self) -> dict:
        """Get 24h volume for all pairs."""
        url = f"{self.base_url}/ticker/24hr"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        volumes = {}
        for ticker in data:
            if ticker['symbol'].endswith('USDT'):
                volumes[ticker['symbol']] = float(ticker['quoteVolume'])

        return volumes

    def get_klines(self, symbol: str, interval: str = '1w', limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV data from Binance."""
        url = f"{self.base_url}/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

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
