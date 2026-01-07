#!/usr/bin/env python3
"""
LeveLeledc (InSilico) Scanner
Scans crypto pairs for exhaustion signals on weekly/2-weekly timeframes.

Supported exchanges: Binance, Coinbase, Kraken

Based on the Pine Script indicator by InSilico:
https://www.tradingview.com/script/2rZDPyaC-Leledc-Exhaustion-Bar/
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import argparse
import json

from exchanges import get_exchange, list_exchanges

# Configuration
MIN_VOLUME_USD = 1_000_000  # Minimum 24h volume in USD to filter dead coins


# =============================================================================
# TECHNICAL INDICATOR FUNCTIONS
# =============================================================================

def calculate_ema(prices, period):
    """Calculate Exponential Moving Average."""
    if len(prices) < period:
        return np.full(len(prices), np.nan)

    ema = np.zeros(len(prices))
    ema[:] = np.nan

    # Start with SMA
    ema[period-1] = np.mean(prices[:period])

    # EMA multiplier
    multiplier = 2 / (period + 1)

    # Calculate EMA
    for i in range(period, len(prices)):
        ema[i] = (prices[i] - ema[i-1]) * multiplier + ema[i-1]

    return ema


def calculate_rsi(prices, period=14):
    """Calculate Relative Strength Index."""
    if len(prices) < period + 1:
        return np.full(len(prices), np.nan)

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    rsi = np.zeros(len(prices))
    rsi[:] = np.nan

    # Initial average gain/loss
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        rsi[period] = 100
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100 - (100 / (1 + rs))

    # Calculate RSI using smoothed averages
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi[i + 1] = 100
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100 - (100 / (1 + rs))

    return rsi


def calculate_bmsb(closes):
    """Calculate Bull Market Support Band (20 & 21 week EMA)."""
    ema20 = calculate_ema(closes, 20)
    ema21 = calculate_ema(closes, 21)
    return ema20, ema21


def calculate_ema_200(closes):
    """Calculate 200 period EMA."""
    return calculate_ema(closes, 200)


# =============================================================================
# CONFIRMATION ANALYSIS FUNCTIONS
# =============================================================================

def analyze_confirmations(df, signal_type, signal_bar_idx, support_level, resistance_level):
    """
    Analyze confirmations for a signal.

    Args:
        df: DataFrame with OHLCV data
        signal_type: 'bullish' or 'bearish'
        signal_bar_idx: Index of the signal bar (from end, 0 = current bar)
        support_level: Support level from the signal
        resistance_level: Resistance level from the signal

    Returns:
        dict with confirmation analysis
    """
    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    volumes = df['volume'].values

    n = len(df)
    signal_idx = n - 1 - signal_bar_idx  # Convert to actual index

    confirmations = {
        'support_reclaim': None,
        'resistance_reject': None,
        'above_bmsb': None,
        'below_bmsb': None,
        'volume_spike': None,
        'volume_ratio': None,
        'rsi_divergence': None,
        'ema_200_distance': None,
        'weekly_close_type': None,  # 'closed_above' or 'wicked_above'
        'key_level': None,
        'confirmation_count': 0,
        'status': 'developing'  # 'confirmed', 'developing', 'failed'
    }

    # Calculate indicators
    ema20, ema21 = calculate_bmsb(closes)
    rsi = calculate_rsi(closes, 14)
    ema200 = calculate_ema_200(closes)

    # Average volume (20 period)
    avg_volume = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)

    if signal_type == 'bullish':
        key_level = support_level if not np.isnan(support_level) else lows[signal_idx]
        confirmations['key_level'] = key_level

        # 1. Support Reclaim Check
        # Look at bars after signal to see if price dipped below then reclaimed
        if signal_bar_idx > 0:  # Signal is not on current bar
            bars_after_signal = range(signal_idx + 1, n)
            dipped_below = False
            reclaimed = False

            for i in bars_after_signal:
                if lows[i] < key_level:
                    dipped_below = True
                if dipped_below and closes[i] > key_level:
                    reclaimed = True
                    break

            if reclaimed:
                confirmations['support_reclaim'] = True
                confirmations['confirmation_count'] += 1
            elif dipped_below:
                confirmations['support_reclaim'] = False  # Dipped but not reclaimed yet
            else:
                confirmations['support_reclaim'] = None  # Not yet tested

        # 2. BMSB Check (Bull Market Support Band)
        current_close = closes[-1]
        bmsb_top = max(ema20[-1], ema21[-1]) if not np.isnan(ema20[-1]) else None
        bmsb_bottom = min(ema20[-1], ema21[-1]) if not np.isnan(ema20[-1]) else None

        if bmsb_top is not None:
            if current_close > bmsb_top:
                confirmations['above_bmsb'] = True
                confirmations['confirmation_count'] += 1
            elif current_close > bmsb_bottom:
                confirmations['above_bmsb'] = 'within'  # Within the band
            else:
                confirmations['above_bmsb'] = False

        # 3. Volume Confirmation
        if signal_bar_idx < len(volumes):
            # Check volume on reclaim candle or most recent candle
            check_idx = -1 if signal_bar_idx == 0 else signal_idx + 1
            if check_idx < n:
                vol_ratio = volumes[check_idx] / avg_volume if avg_volume > 0 else 0
                confirmations['volume_ratio'] = round(vol_ratio, 2)
                if vol_ratio > 1.5:
                    confirmations['volume_spike'] = True
                    confirmations['confirmation_count'] += 1
                else:
                    confirmations['volume_spike'] = False

        # 4. Weekly Close Type
        current_close = closes[-1]
        current_high = highs[-1]
        if current_close > key_level:
            confirmations['weekly_close_type'] = 'closed_above'
        elif current_high > key_level:
            confirmations['weekly_close_type'] = 'wicked_above'
        else:
            confirmations['weekly_close_type'] = 'below'

        # 5. RSI Divergence (bullish = price lower low, RSI higher low)
        if signal_idx >= 5 and not np.isnan(rsi[signal_idx]):
            # Look back for divergence
            lookback = min(10, signal_idx)
            price_lows_idx = []
            rsi_at_lows = []

            for i in range(signal_idx - lookback, signal_idx + 1):
                if i > 0 and i < n - 1:
                    if lows[i] < lows[i-1] and lows[i] < lows[i+1]:  # Local low
                        price_lows_idx.append(i)
                        rsi_at_lows.append(rsi[i])

            if len(price_lows_idx) >= 2:
                # Check if price made lower low but RSI made higher low
                if lows[price_lows_idx[-1]] < lows[price_lows_idx[-2]] and rsi_at_lows[-1] > rsi_at_lows[-2]:
                    confirmations['rsi_divergence'] = True
                    confirmations['confirmation_count'] += 1
                else:
                    confirmations['rsi_divergence'] = False

        # 6. Distance from 200 EMA
        if not np.isnan(ema200[-1]) and ema200[-1] > 0:
            distance_pct = ((current_close - ema200[-1]) / ema200[-1]) * 100
            confirmations['ema_200_distance'] = round(distance_pct, 1)
            if distance_pct < -30:  # Extremely oversold
                confirmations['confirmation_count'] += 1

    elif signal_type == 'bearish':
        key_level = resistance_level if not np.isnan(resistance_level) else highs[signal_idx]
        confirmations['key_level'] = key_level

        # 1. Resistance Reject Check
        if signal_bar_idx > 0:
            bars_after_signal = range(signal_idx + 1, n)
            wicked_above = False
            rejected = False

            for i in bars_after_signal:
                if highs[i] > key_level:
                    wicked_above = True
                if wicked_above and closes[i] < key_level:
                    rejected = True
                    break

            if rejected:
                confirmations['resistance_reject'] = True
                confirmations['confirmation_count'] += 1
            elif wicked_above:
                confirmations['resistance_reject'] = False
            else:
                confirmations['resistance_reject'] = None

        # 2. BMSB Check (should be below for bearish)
        current_close = closes[-1]
        bmsb_bottom = min(ema20[-1], ema21[-1]) if not np.isnan(ema20[-1]) else None

        if bmsb_bottom is not None:
            if current_close < bmsb_bottom:
                confirmations['below_bmsb'] = True
                confirmations['confirmation_count'] += 1
            else:
                confirmations['below_bmsb'] = False

        # 3. Volume Confirmation (same logic)
        if signal_bar_idx < len(volumes):
            check_idx = -1 if signal_bar_idx == 0 else signal_idx + 1
            if check_idx < n:
                vol_ratio = volumes[check_idx] / avg_volume if avg_volume > 0 else 0
                confirmations['volume_ratio'] = round(vol_ratio, 2)
                if vol_ratio > 1.5:
                    confirmations['volume_spike'] = True
                    confirmations['confirmation_count'] += 1
                else:
                    confirmations['volume_spike'] = False

        # 4. Weekly Close Type
        current_close = closes[-1]
        current_low = lows[-1]
        if current_close < key_level:
            confirmations['weekly_close_type'] = 'closed_below'
        elif current_low < key_level:
            confirmations['weekly_close_type'] = 'wicked_below'
        else:
            confirmations['weekly_close_type'] = 'above'

        # 5. RSI Divergence (bearish = price higher high, RSI lower high)
        if signal_idx >= 5 and not np.isnan(rsi[signal_idx]):
            lookback = min(10, signal_idx)
            price_highs_idx = []
            rsi_at_highs = []

            for i in range(signal_idx - lookback, signal_idx + 1):
                if i > 0 and i < n - 1:
                    if highs[i] > highs[i-1] and highs[i] > highs[i+1]:  # Local high
                        price_highs_idx.append(i)
                        rsi_at_highs.append(rsi[i])

            if len(price_highs_idx) >= 2:
                if highs[price_highs_idx[-1]] > highs[price_highs_idx[-2]] and rsi_at_highs[-1] < rsi_at_highs[-2]:
                    confirmations['rsi_divergence'] = True
                    confirmations['confirmation_count'] += 1
                else:
                    confirmations['rsi_divergence'] = False

        # 6. Distance from 200 EMA
        if not np.isnan(ema200[-1]) and ema200[-1] > 0:
            distance_pct = ((current_close - ema200[-1]) / ema200[-1]) * 100
            confirmations['ema_200_distance'] = round(distance_pct, 1)
            if distance_pct > 30:  # Extremely overbought
                confirmations['confirmation_count'] += 1

    # Determine status
    if confirmations['confirmation_count'] >= 2:
        confirmations['status'] = 'confirmed'
    elif signal_bar_idx == 0:
        confirmations['status'] = 'new'
    else:
        confirmations['status'] = 'developing'

    return confirmations


# =============================================================================
# CORE SCANNER FUNCTIONS
# =============================================================================

def resample_to_2w(df):
    """Resample weekly data to 2-week candles."""
    if df is None or len(df) < 4:
        return None
    
    df = df.set_index('open_time')
    
    # Resample to 2-week periods
    resampled = df.resample('2W').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    resampled = resampled.reset_index()
    return resampled


def calculate_leledc(df, length=40, bars=10):
    """
    Calculate LeveLeledc exhaustion signals.
    
    Returns:
        dict with 'bullish' and 'bearish' signals for the most recent bar,
        plus 'resistance' and 'support' levels.
    """
    if df is None or len(df) < max(length, bars + 5):
        return None
    
    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    
    n = len(df)
    bindex = 0
    sindex = 0
    
    signals = []
    resistance = np.nan
    support = np.nan
    
    for i in range(4, n):
        # Update counters
        if closes[i] > closes[i-4]:
            bindex += 1
        if closes[i] < closes[i-4]:
            sindex += 1
        
        signal = 0
        
        # Check for bearish exhaustion (top)
        if i >= length:
            highest_high = max(highs[i-length+1:i+1])
            lowest_low = min(lows[i-length+1:i+1])
            
            if bindex > bars and closes[i] < opens[i] and highs[i] >= highest_high:
                bindex = 0
                signal = -1
                resistance = highs[i]
            elif sindex > bars and closes[i] > opens[i] and lows[i] <= lowest_low:
                sindex = 0
                signal = 1
                support = lows[i]
        
        signals.append(signal)
    
    # Check the last few bars for recent signals
    recent_signals = signals[-3:] if len(signals) >= 3 else signals
    
    result = {
        'latest_signal': signals[-1] if signals else 0,
        'recent_bullish': 1 in recent_signals,
        'recent_bearish': -1 in recent_signals,
        'resistance': resistance,
        'support': support,
        'current_price': closes[-1],
        'bars_analyzed': len(df)
    }
    
    # Find how many bars ago the last signal was
    for i, sig in enumerate(reversed(signals)):
        if sig == 1:
            result['bars_since_bullish'] = i
            break
    else:
        result['bars_since_bullish'] = None
        
    for i, sig in enumerate(reversed(signals)):
        if sig == -1:
            result['bars_since_bearish'] = i
            break
    else:
        result['bars_since_bearish'] = None
    
    return result


def scan_symbol(symbol, exchange, timeframe='1w'):
    """Scan a single symbol for LeveLeledc signals."""
    time.sleep(exchange.rate_limit_delay)

    if timeframe == '2w':
        df = exchange.get_klines(symbol, '1w', limit=200)
        df = resample_to_2w(df)
    else:
        df = exchange.get_klines(symbol, timeframe, limit=100)

    if df is None:
        return None

    result = calculate_leledc(df)
    if result:
        result['symbol'] = symbol
        result['display_symbol'] = exchange.display_symbol(symbol)
        result['timeframe'] = timeframe

        # Add confirmation analysis for signals
        if result['latest_signal'] == 1:  # Bullish now
            result['confirmations'] = analyze_confirmations(
                df, 'bullish', 0, result['support'], result['resistance']
            )
        elif result['latest_signal'] == -1:  # Bearish now
            result['confirmations'] = analyze_confirmations(
                df, 'bearish', 0, result['support'], result['resistance']
            )
        elif result['recent_bullish'] and result['bars_since_bullish'] is not None:
            result['confirmations'] = analyze_confirmations(
                df, 'bullish', result['bars_since_bullish'], result['support'], result['resistance']
            )
        elif result['recent_bearish'] and result['bars_since_bearish'] is not None:
            result['confirmations'] = analyze_confirmations(
                df, 'bearish', result['bars_since_bearish'], result['support'], result['resistance']
            )

    return result


def scan_all(exchange_name='binance', timeframe='1w', min_volume=MIN_VOLUME_USD, max_workers=5):
    """Scan all pairs on an exchange for signals."""
    exchange = get_exchange(exchange_name)

    print(f"Exchange: {exchange.name.upper()} ({exchange.quote_asset} pairs)")
    print(f"Fetching trading pairs...")
    all_pairs = exchange.get_all_pairs()
    print(f"Found {len(all_pairs)} {exchange.quote_asset} trading pairs")

    print(f"Fetching 24h volumes...")
    volumes = exchange.get_24h_volumes()

    # Filter by volume
    active_pairs = [p for p in all_pairs if volumes.get(p, 0) >= min_volume]
    print(f"Filtered to {len(active_pairs)} pairs with ≥${min_volume:,.0f} 24h volume")

    print(f"\nScanning on {timeframe} timeframe...")

    results = {
        'exchange': exchange.name,
        'bullish_now': [],      # Signal on current bar
        'bearish_now': [],      # Signal on current bar
        'bullish_recent': [],   # Signal in last 3 bars
        'bearish_recent': [],   # Signal in last 3 bars
        'errors': []
    }

    completed = 0
    total = len(active_pairs)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {
            executor.submit(scan_symbol, symbol, exchange, timeframe): symbol
            for symbol in active_pairs
        }

        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            completed += 1

            if completed % 50 == 0:
                print(f"Progress: {completed}/{total} ({100*completed/total:.1f}%)")

            try:
                result = future.result()
                if result is None:
                    continue

                if result['latest_signal'] == 1:
                    results['bullish_now'].append(result)
                elif result['latest_signal'] == -1:
                    results['bearish_now'].append(result)
                elif result['recent_bullish']:
                    results['bullish_recent'].append(result)
                elif result['recent_bearish']:
                    results['bearish_recent'].append(result)

            except Exception as e:
                results['errors'].append({'symbol': symbol, 'error': str(e)})

    return results


def print_results(results, timeframe):
    """Pretty print the scan results."""
    exchange = results.get('exchange', 'unknown').upper()

    # ==========================================================================
    # RAW SIGNALS SECTION (unchanged)
    # ==========================================================================
    print("\n" + "="*70)
    print(f"{'RAW SIGNALS':^70}")
    print(f"LELEDC SCANNER - {exchange} - {timeframe.upper()} TIMEFRAME")
    print("="*70)

    if results['bullish_now']:
        print(f"\n🟢 BULLISH EXHAUSTION - CURRENT BAR ({len(results['bullish_now'])} found)")
        print("-"*50)
        for r in sorted(results['bullish_now'], key=lambda x: x.get('display_symbol', x['symbol'])):
            sym = r.get('display_symbol', r['symbol'])
            print(f"  {sym:<12} Price: ${r['current_price']:.8g}")

    if results['bearish_now']:
        print(f"\n🔴 BEARISH EXHAUSTION - CURRENT BAR ({len(results['bearish_now'])} found)")
        print("-"*50)
        for r in sorted(results['bearish_now'], key=lambda x: x.get('display_symbol', x['symbol'])):
            sym = r.get('display_symbol', r['symbol'])
            print(f"  {sym:<12} Price: ${r['current_price']:.8g}")

    if results['bullish_recent']:
        print(f"\n🟡 BULLISH EXHAUSTION - RECENT (last 3 bars) ({len(results['bullish_recent'])} found)")
        print("-"*50)
        for r in sorted(results['bullish_recent'], key=lambda x: x.get('display_symbol', x['symbol'])):
            sym = r.get('display_symbol', r['symbol'])
            bars_ago = r.get('bars_since_bullish', '?')
            print(f"  {sym:<12} Price: ${r['current_price']:.8g}  ({bars_ago} bars ago)")

    if results['bearish_recent']:
        print(f"\n🟠 BEARISH EXHAUSTION - RECENT (last 3 bars) ({len(results['bearish_recent'])} found)")
        print("-"*50)
        for r in sorted(results['bearish_recent'], key=lambda x: x.get('display_symbol', x['symbol'])):
            sym = r.get('display_symbol', r['symbol'])
            bars_ago = r.get('bars_since_bearish', '?')
            print(f"  {sym:<12} Price: ${r['current_price']:.8g}  ({bars_ago} bars ago)")

    if not any([results['bullish_now'], results['bearish_now'],
                results['bullish_recent'], results['bearish_recent']]):
        print("\nNo exhaustion signals found.")
        print(f"\n{'='*70}")
        if results['errors']:
            print(f"Errors encountered: {len(results['errors'])}")
        return

    # ==========================================================================
    # CONFIRMATION ANALYSIS SECTION
    # ==========================================================================
    print("\n" + "="*70)
    print(f"{'CONFIRMATION ANALYSIS':^70}")
    print("="*70)

    # Combine all signals for analysis
    all_bullish = results['bullish_now'] + results['bullish_recent']
    all_bearish = results['bearish_now'] + results['bearish_recent']

    # Categorize by confirmation status
    confirmed_bullish = [r for r in all_bullish if r.get('confirmations', {}).get('status') == 'confirmed']
    confirmed_bearish = [r for r in all_bearish if r.get('confirmations', {}).get('status') == 'confirmed']
    developing_bullish = [r for r in all_bullish if r.get('confirmations', {}).get('status') in ('developing', 'new')]
    developing_bearish = [r for r in all_bearish if r.get('confirmations', {}).get('status') in ('developing', 'new')]

    # Print confirmed signals
    if confirmed_bullish or confirmed_bearish:
        print(f"\n🟢 CONFIRMED BULLISH (2+ confirmations)" if confirmed_bullish else "")
        for r in sorted(confirmed_bullish, key=lambda x: x.get('confirmations', {}).get('confirmation_count', 0), reverse=True):
            print_confirmation_detail(r, 'bullish')

        if confirmed_bearish:
            print(f"\n🔴 CONFIRMED BEARISH (2+ confirmations)")
        for r in sorted(confirmed_bearish, key=lambda x: x.get('confirmations', {}).get('confirmation_count', 0), reverse=True):
            print_confirmation_detail(r, 'bearish')

    # Print developing signals
    if developing_bullish or developing_bearish:
        if developing_bullish:
            print(f"\n🟡 DEVELOPING BULLISH (watching for confirmation)")
        for r in developing_bullish:
            print_confirmation_detail(r, 'bullish')

        if developing_bearish:
            print(f"\n🟠 DEVELOPING BEARISH (watching for confirmation)")
        for r in developing_bearish:
            print_confirmation_detail(r, 'bearish')

    # ==========================================================================
    # CONFIRMATION SUMMARY
    # ==========================================================================
    print("\n" + "-"*70)
    print("📋 CONFIRMATION SUMMARY")
    print("-"*70)

    # 3+ confirmations
    strong_bullish = [r.get('display_symbol', r['symbol']) for r in all_bullish
                      if r.get('confirmations', {}).get('confirmation_count', 0) >= 3]
    strong_bearish = [r.get('display_symbol', r['symbol']) for r in all_bearish
                      if r.get('confirmations', {}).get('confirmation_count', 0) >= 3]

    if strong_bullish:
        print(f"  Bullish with 3+ confirmations: {', '.join(strong_bullish)}")
    if strong_bearish:
        print(f"  Bearish with 3+ confirmations: {', '.join(strong_bearish)}")

    # RSI divergence
    rsi_div_bullish = [r.get('display_symbol', r['symbol']) for r in all_bullish
                       if r.get('confirmations', {}).get('rsi_divergence') == True]
    rsi_div_bearish = [r.get('display_symbol', r['symbol']) for r in all_bearish
                       if r.get('confirmations', {}).get('rsi_divergence') == True]

    if rsi_div_bullish:
        print(f"  Bullish RSI divergence: {', '.join(rsi_div_bullish)}")
    if rsi_div_bearish:
        print(f"  Bearish RSI divergence: {', '.join(rsi_div_bearish)}")

    # BMSB reclaims
    bmsb_bullish = [r.get('display_symbol', r['symbol']) for r in all_bullish
                    if r.get('confirmations', {}).get('above_bmsb') == True]
    bmsb_bearish = [r.get('display_symbol', r['symbol']) for r in all_bearish
                    if r.get('confirmations', {}).get('below_bmsb') == True]

    if bmsb_bullish:
        print(f"  Above BMSB (bullish): {', '.join(bmsb_bullish)}")
    if bmsb_bearish:
        print(f"  Below BMSB (bearish): {', '.join(bmsb_bearish)}")

    # Extremely oversold/overbought
    oversold = [f"{r.get('display_symbol', r['symbol'])} ({r.get('confirmations', {}).get('ema_200_distance')}%)"
                for r in all_bullish
                if r.get('confirmations', {}).get('ema_200_distance') is not None
                and r.get('confirmations', {}).get('ema_200_distance') < -30]
    overbought = [f"{r.get('display_symbol', r['symbol'])} ({r.get('confirmations', {}).get('ema_200_distance')}%)"
                  for r in all_bearish
                  if r.get('confirmations', {}).get('ema_200_distance') is not None
                  and r.get('confirmations', {}).get('ema_200_distance') > 30]

    if oversold:
        print(f"  Extremely oversold (>30% below 200 EMA): {', '.join(oversold)}")
    if overbought:
        print(f"  Extremely overbought (>30% above 200 EMA): {', '.join(overbought)}")

    print(f"\n{'='*70}")
    if results['errors']:
        print(f"Errors encountered: {len(results['errors'])}")


def print_confirmation_detail(r, signal_type):
    """Print detailed confirmation info for a signal."""
    sym = r.get('display_symbol', r['symbol'])
    conf = r.get('confirmations', {})
    bars_ago = r.get('bars_since_bullish' if signal_type == 'bullish' else 'bars_since_bearish', 0)

    signal_desc = "Current bar" if bars_ago == 0 else f"{bars_ago} bars ago"
    key_level = conf.get('key_level')
    key_level_str = f"${key_level:.8g}" if key_level else "N/A"

    print(f"\n  {sym} - Signal: {signal_desc}")

    # Support/Resistance reclaim
    if signal_type == 'bullish':
        sr = conf.get('support_reclaim')
        if sr == True:
            print(f"    ✓ Support reclaim ({key_level_str})")
        elif sr == False:
            print(f"    ✗ Support broken, not reclaimed ({key_level_str})")
        else:
            print(f"    ~ Support not yet tested ({key_level_str})")
    else:
        rr = conf.get('resistance_reject')
        if rr == True:
            print(f"    ✓ Resistance rejected ({key_level_str})")
        elif rr == False:
            print(f"    ✗ Above resistance ({key_level_str})")
        else:
            print(f"    ~ Resistance not yet tested ({key_level_str})")

    # BMSB
    if signal_type == 'bullish':
        bmsb = conf.get('above_bmsb')
        if bmsb == True:
            print(f"    ✓ Above BMSB")
        elif bmsb == 'within':
            print(f"    ~ Within BMSB band")
        elif bmsb == False:
            print(f"    ✗ Below BMSB")
    else:
        bmsb = conf.get('below_bmsb')
        if bmsb == True:
            print(f"    ✓ Below BMSB")
        elif bmsb == False:
            print(f"    ✗ Above BMSB")

    # Volume
    vol_spike = conf.get('volume_spike')
    vol_ratio = conf.get('volume_ratio', 0)
    if vol_spike == True:
        print(f"    ✓ Volume spike ({vol_ratio}x avg)")
    elif vol_spike == False:
        print(f"    ✗ No volume spike ({vol_ratio}x avg)")

    # RSI Divergence
    rsi_div = conf.get('rsi_divergence')
    if rsi_div == True:
        print(f"    ✓ RSI divergence")
    elif rsi_div == False:
        print(f"    ✗ No RSI divergence")

    # Weekly close type
    close_type = conf.get('weekly_close_type', '')
    if 'closed' in close_type:
        print(f"    ✓ Weekly {close_type.replace('_', ' ')}")
    elif 'wicked' in close_type:
        print(f"    ~ Weekly {close_type.replace('_', ' ')}")

    # 200 EMA distance
    ema_dist = conf.get('ema_200_distance')
    if ema_dist is not None:
        if (signal_type == 'bullish' and ema_dist < -30) or (signal_type == 'bearish' and ema_dist > 30):
            print(f"    ✓ Extended from 200 EMA ({ema_dist:+.1f}%)")
        else:
            print(f"    • Distance from 200 EMA: {ema_dist:+.1f}%")


def export_results(results, timeframe, filename=None):
    """Export results to JSON."""
    exchange = results.get('exchange', 'unknown')
    if filename is None:
        filename = f"leledc_scan_{exchange}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    export_data = {
        'scan_time': datetime.now().isoformat(),
        'exchange': exchange,
        'timeframe': timeframe,
        'results': results
    }

    with open(filename, 'w') as f:
        json.dump(export_data, f, indent=2, default=str)

    print(f"\nResults exported to: {filename}")
    return filename


def print_multi_timeframe_confluence(all_results_by_tf):
    """Print multi-timeframe confluence analysis."""
    if len(all_results_by_tf) < 2:
        return

    print("\n" + "="*70)
    print(f"{'MULTI-TIMEFRAME CONFLUENCE':^70}")
    print("="*70)

    # Collect symbols with signals from each timeframe
    tf_signals = {}
    for tf, results in all_results_by_tf.items():
        bullish = set()
        bearish = set()

        for r in results.get('bullish_now', []) + results.get('bullish_recent', []):
            bullish.add(r.get('display_symbol', r['symbol']))
        for r in results.get('bearish_now', []) + results.get('bearish_recent', []):
            bearish.add(r.get('display_symbol', r['symbol']))

        tf_signals[tf] = {'bullish': bullish, 'bearish': bearish}

    # Find confluence (signals in multiple timeframes)
    all_tfs = list(tf_signals.keys())

    # Bullish confluence
    bullish_confluence = set.intersection(*[tf_signals[tf]['bullish'] for tf in all_tfs]) if all_tfs else set()
    bearish_confluence = set.intersection(*[tf_signals[tf]['bearish'] for tf in all_tfs]) if all_tfs else set()

    if bullish_confluence:
        print(f"\n🟢 BULLISH on BOTH timeframes ({', '.join(all_tfs).upper()}):")
        print(f"   {', '.join(sorted(bullish_confluence))}")
        print("   ⚡ These have the strongest confluence!")

    if bearish_confluence:
        print(f"\n🔴 BEARISH on BOTH timeframes ({', '.join(all_tfs).upper()}):")
        print(f"   {', '.join(sorted(bearish_confluence))}")
        print("   ⚡ These have the strongest confluence!")

    # Mixed signals (bullish on one, bearish on other)
    if len(all_tfs) == 2:
        tf1, tf2 = all_tfs
        mixed_1 = tf_signals[tf1]['bullish'] & tf_signals[tf2]['bearish']
        mixed_2 = tf_signals[tf1]['bearish'] & tf_signals[tf2]['bullish']

        if mixed_1 or mixed_2:
            print(f"\n⚠️  MIXED SIGNALS (conflicting timeframes):")
            for sym in sorted(mixed_1 | mixed_2):
                if sym in mixed_1:
                    print(f"   {sym}: Bullish {tf1.upper()}, Bearish {tf2.upper()}")
                else:
                    print(f"   {sym}: Bearish {tf1.upper()}, Bullish {tf2.upper()}")

    if not bullish_confluence and not bearish_confluence:
        print("\n   No multi-timeframe confluence found.")

    print(f"\n{'='*70}")


def main():
    available_exchanges = ', '.join(list_exchanges())

    parser = argparse.ArgumentParser(description='Scan crypto pairs for LeveLeledc exhaustion signals')
    parser.add_argument('-x', '--exchange', choices=list_exchanges(),
                        help=f'Scan only this exchange. Available: {available_exchanges}')
    parser.add_argument('-t', '--timeframe', choices=['1w', '2w'],
                        help='Scan only this timeframe (1w=weekly, 2w=bi-weekly)')
    parser.add_argument('-v', '--min-volume', type=float, default=MIN_VOLUME_USD,
                        help=f'Minimum 24h USD volume (default: {MIN_VOLUME_USD:,.0f})')
    parser.add_argument('-w', '--workers', type=int, default=5,
                        help='Number of parallel workers (default: 5)')
    parser.add_argument('-e', '--export', action='store_true',
                        help='Export results to JSON file')
    parser.add_argument('--both', action='store_true',
                        help='Scan both 1w and 2w timeframes (default if no -t specified)')

    args = parser.parse_args()

    # Default: all exchanges, both timeframes
    timeframes = [args.timeframe] if args.timeframe else ['1w', '2w']
    if args.both:
        timeframes = ['1w', '2w']
    exchanges = [args.exchange] if args.exchange else list_exchanges()

    for exchange in exchanges:
        # Track results across timeframes for multi-TF analysis
        all_results_by_tf = {}

        for tf in timeframes:
            print(f"\n{'#'*60}")
            print(f"Starting scan: {exchange.upper()} - {tf} timeframe...")
            print(f"{'#'*60}\n")

            results = scan_all(
                exchange_name=exchange,
                timeframe=tf,
                min_volume=args.min_volume,
                max_workers=args.workers
            )

            all_results_by_tf[tf] = results
            print_results(results, tf)

            if args.export:
                export_results(results, tf)

        # Print multi-timeframe confluence if we scanned multiple timeframes
        if len(timeframes) > 1:
            print_multi_timeframe_confluence(all_results_by_tf)


if __name__ == '__main__':
    main()
