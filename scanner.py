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

    print("\n" + "="*60)
    print(f"LELEDC SCANNER RESULTS - {exchange} - {timeframe.upper()} TIMEFRAME")
    print("="*60)

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

    print(f"\n{'='*60}")
    if results['errors']:
        print(f"Errors encountered: {len(results['errors'])}")


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


def main():
    available_exchanges = ', '.join(list_exchanges())

    parser = argparse.ArgumentParser(description='Scan crypto pairs for LeveLeledc exhaustion signals')
    parser.add_argument('-x', '--exchange', choices=list_exchanges(), default='binance',
                        help=f'Exchange to scan (default: binance). Available: {available_exchanges}')
    parser.add_argument('-t', '--timeframe', choices=['1w', '2w'], default='1w',
                        help='Timeframe to scan (1w=weekly, 2w=bi-weekly)')
    parser.add_argument('-v', '--min-volume', type=float, default=MIN_VOLUME_USD,
                        help=f'Minimum 24h USD volume (default: {MIN_VOLUME_USD:,.0f})')
    parser.add_argument('-w', '--workers', type=int, default=5,
                        help='Number of parallel workers (default: 5)')
    parser.add_argument('-e', '--export', action='store_true',
                        help='Export results to JSON file')
    parser.add_argument('--both', action='store_true',
                        help='Scan both 1w and 2w timeframes')
    parser.add_argument('--all-exchanges', action='store_true',
                        help='Scan all supported exchanges')

    args = parser.parse_args()

    timeframes = ['1w', '2w'] if args.both else [args.timeframe]
    exchanges = list_exchanges() if args.all_exchanges else [args.exchange]

    for exchange in exchanges:
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

            print_results(results, tf)

            if args.export:
                export_results(results, tf)


if __name__ == '__main__':
    main()
