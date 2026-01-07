#!/usr/bin/env python3
"""
LeveLeledc Scanner - Web UI
A simple Flask web interface for the crypto exhaustion scanner.
"""

from flask import Flask, render_template, jsonify, request, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
import json

from exchanges import get_exchange, list_exchanges

app = Flask(__name__)

# Scanner configuration
MIN_VOLUME_USD = 1_000_000


def resample_to_2w(df):
    """Resample weekly data to 2-week candles."""
    if df is None or len(df) < 4:
        return None

    df = df.set_index('open_time')

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
    """Calculate LeveLeledc exhaustion signals."""
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
    resistance = None
    support = None

    for i in range(4, n):
        if closes[i] > closes[i-4]:
            bindex += 1
        if closes[i] < closes[i-4]:
            sindex += 1

        signal = 0

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


def run_scan(exchange_name, timeframe, min_volume, max_workers=5):
    """Run the scanner on an exchange."""
    exchange = get_exchange(exchange_name)

    all_pairs = exchange.get_all_pairs()
    volumes = exchange.get_24h_volumes()

    active_pairs = [p for p in all_pairs if volumes.get(p, 0) >= min_volume]

    results = {
        'exchange': exchange.name,
        'timeframe': timeframe,
        'total_pairs': len(all_pairs),
        'filtered_pairs': len(active_pairs),
        'bullish_now': [],
        'bearish_now': [],
        'bullish_recent': [],
        'bearish_recent': [],
        'errors': []
    }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_symbol = {
            executor.submit(scan_symbol, symbol, exchange, timeframe): symbol
            for symbol in active_pairs
        }

        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]

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

    # Sort results by symbol
    for key in ['bullish_now', 'bearish_now', 'bullish_recent', 'bearish_recent']:
        results[key] = sorted(results[key], key=lambda x: x.get('display_symbol', x['symbol']))

    return results


@app.route('/')
def index():
    """Render the main page."""
    exchanges = list_exchanges()
    return render_template('index.html', exchanges=exchanges)


@app.route('/api/scan')
def api_scan():
    """API endpoint to run a scan with SSE progress updates."""
    exchange_name = request.args.get('exchange', 'binance')
    timeframe = request.args.get('timeframe', '1w')
    min_volume = float(request.args.get('min_volume', MIN_VOLUME_USD))

    def generate():
        try:
            exchange = get_exchange(exchange_name)

            # Phase 1: Fetching pairs
            yield f"data: {json.dumps({'type': 'status', 'message': 'Fetching trading pairs...'})}\n\n"
            all_pairs = exchange.get_all_pairs()

            # Phase 2: Fetching volumes
            yield f"data: {json.dumps({'type': 'status', 'message': 'Fetching 24h volumes...'})}\n\n"
            volumes = exchange.get_24h_volumes()

            # Filter pairs
            active_pairs = [p for p in all_pairs if volumes.get(p, 0) >= min_volume]
            total = len(active_pairs)

            yield f"data: {json.dumps({'type': 'init', 'total': total, 'exchange': exchange.name})}\n\n"

            results = {
                'exchange': exchange.name,
                'timeframe': timeframe,
                'total_pairs': len(all_pairs),
                'filtered_pairs': total,
                'bullish_now': [],
                'bearish_now': [],
                'bullish_recent': [],
                'bearish_recent': [],
                'errors': []
            }

            completed = 0

            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_symbol = {
                    executor.submit(scan_symbol, symbol, exchange, timeframe): symbol
                    for symbol in active_pairs
                }

                for future in as_completed(future_to_symbol):
                    symbol = future_to_symbol[future]
                    completed += 1

                    # Send progress update
                    yield f"data: {json.dumps({'type': 'progress', 'completed': completed, 'total': total, 'symbol': exchange.display_symbol(symbol)})}\n\n"

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

            # Sort results
            for key in ['bullish_now', 'bearish_now', 'bullish_recent', 'bearish_recent']:
                results[key] = sorted(results[key], key=lambda x: x.get('display_symbol', x['symbol']))

            # Send final results
            yield f"data: {json.dumps({'type': 'complete', 'results': results})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/exchanges')
def api_exchanges():
    """Get list of supported exchanges."""
    return jsonify({'exchanges': list_exchanges()})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
