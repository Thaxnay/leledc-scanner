"""Kraken exchange adapter."""

import requests
import pandas as pd
from .base import BaseExchange


class KrakenExchange(BaseExchange):
    """Kraken exchange adapter."""

    name = "kraken"
    quote_asset = "USD"
    base_url = "https://api.kraken.com/0/public"
    rate_limit_delay = 0.2  # Kraken has stricter rate limits

    # Kraken intervals are in minutes
    INTERVAL_MAP = {
        '1m': 1,
        '5m': 5,
        '15m': 15,
        '30m': 30,
        '1h': 60,
        '4h': 240,
        '1d': 1440,
        '1w': 10080,
    }

    def __init__(self):
        super().__init__()
        self._pair_map = {}  # Maps display name -> Kraken pair name
        self._wsname_map = {}  # Maps wsname -> Kraken pair name

    def get_all_pairs(self) -> list:
        """Fetch all USD trading pairs from Kraken."""
        url = f"{self.base_url}/AssetPairs"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data.get('error'):
            raise Exception(f"Kraken API error: {data['error']}")

        usd_pairs = []
        for pair_name, pair_info in data.get('result', {}).items():
            # Filter for USD quote pairs (Kraken uses ZUSD, USD, or .d suffix for derivatives)
            quote = pair_info.get('quote', '')
            wsname = pair_info.get('wsname', '')

            # Skip derivatives and staking pairs
            if '.d' in pair_name or pair_name.endswith('.S'):
                continue

            # Check for USD pairs
            if quote in ('ZUSD', 'USD') or wsname.endswith('/USD'):
                usd_pairs.append(pair_name)
                # Store mapping for display
                if wsname:
                    display = wsname.replace('/', '')
                    self._pair_map[display] = pair_name
                    self._wsname_map[wsname] = pair_name

        return usd_pairs

    def get_24h_volumes(self) -> dict:
        """Get 24h volume for all pairs."""
        url = f"{self.base_url}/Ticker"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data.get('error'):
            return {}

        volumes = {}
        for pair_name, ticker in data.get('result', {}).items():
            try:
                # Kraken ticker format: v = [today_volume, 24h_volume]
                # c = [price, lot_volume] for last trade
                volume_24h = float(ticker['v'][1])
                last_price = float(ticker['c'][0])
                # Volume in quote currency
                volumes[pair_name] = volume_24h * last_price
            except (KeyError, IndexError, ValueError):
                continue

        return volumes

    def get_klines(self, symbol: str, interval: str = '1w', limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV data from Kraken."""
        url = f"{self.base_url}/OHLC"

        kraken_interval = self.INTERVAL_MAP.get(interval, 10080)

        params = {
            'pair': symbol,
            'interval': kraken_interval,
        }

        try:
            response = requests.get(url, params=params, timeout=15)
            data = response.json()

            if data.get('error'):
                return None

            result = data.get('result', {})
            # Remove 'last' key which contains timestamp
            result.pop('last', None)

            if not result:
                return None

            # Get the first (and should be only) pair data
            pair_data = list(result.values())[0]

            if not pair_data:
                return None

            # Kraken OHLC format: [time, open, high, low, close, vwap, volume, count]
            df = pd.DataFrame(pair_data, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'vwap', 'volume', 'count'
            ])

            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            df['open_time'] = pd.to_datetime(df['open_time'], unit='s')

            # Limit to requested number of candles
            if len(df) > limit:
                df = df.tail(limit).reset_index(drop=True)

            return df

        except Exception:
            return None

    def display_symbol(self, symbol: str) -> str:
        """Convert Kraken symbol to display format."""
        # Kraken uses formats like XXBTZUSD, XBT/USD
        # Try to find a cleaner wsname
        for wsname, pair_name in self._wsname_map.items():
            if pair_name == symbol:
                return wsname.replace('/', '')

        # Fallback: strip X and Z prefixes
        clean = symbol
        if clean.startswith('X') and len(clean) > 4:
            clean = clean[1:]
        if 'ZUSD' in clean:
            clean = clean.replace('ZUSD', 'USD')
        elif 'ZEUR' in clean:
            clean = clean.replace('ZEUR', 'EUR')

        return clean
