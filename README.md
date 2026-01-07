# LeveLeledc Scanner

A Python scanner that detects exhaustion signals across all Binance USDT trading pairs using the [LeveLeledc (InSilico)](https://www.tradingview.com/script/2rZDPyaC-Leledc-Exhaustion-Bar/) indicator logic.

## What it does

The scanner identifies potential market tops and bottoms by detecting momentum exhaustion patterns:

- **🟢 Bullish Signal (Green Triangle)**: Sustained selling pressure → new low → bullish candle reversal
- **🔴 Bearish Signal (Red Triangle)**: Sustained buying pressure → new high → bearish candle reversal

## Installation

```bash
cd leledc_scanner
pip install -r requirements.txt
```

## Usage

### Basic scan (weekly timeframe)
```bash
python scanner.py
```

### Scan 2-week timeframe
```bash
python scanner.py -t 2w
```

### Scan both timeframes
```bash
python scanner.py --both
```

### Adjust minimum volume filter
```bash
python scanner.py --min-volume 5000000  # Only coins with >$5M daily volume
```

### Export results to JSON
```bash
python scanner.py --export
```

### All options
```
-t, --timeframe     Timeframe: 1w (weekly) or 2w (bi-weekly)
-v, --min-volume    Minimum 24h USDT volume (default: 1,000,000)
-w, --workers       Parallel workers for faster scanning (default: 5)
-e, --export        Export results to JSON file
--both              Scan both 1w and 2w timeframes
```

## Output

The scanner reports:
1. **Current bar signals** - Exhaustion detected on the most recent completed candle
2. **Recent signals** - Exhaustion within the last 3 bars

Example output:
```
============================================================
LELEDC SCANNER RESULTS - 1W TIMEFRAME
============================================================

🟢 BULLISH EXHAUSTION - CURRENT BAR (2 found)
--------------------------------------------------
  CHZUSDT      Price: $0.044426
  FETUSDT      Price: $1.23

🔴 BEARISH EXHAUSTION - CURRENT BAR (1 found)
--------------------------------------------------
  SOLUSDT      Price: $185.50

🟡 BULLISH EXHAUSTION - RECENT (last 3 bars) (5 found)
--------------------------------------------------
  DOGEUSDT     Price: $0.0823  (2 bars ago)
  ...
```

## The Indicator Logic

Translated from the Pine Script by InSilico:

1. Track momentum: Count consecutive closes above/below the close from 4 bars ago
2. Detect exhaustion when:
   - Momentum count exceeds threshold (default: 10 bars)
   - Price makes a new high/low over lookback period (default: 40 bars)
   - Current candle reverses direction (bearish candle after bullish run, or vice versa)

## Tips

- Weekly timeframe is good for medium-term swing trades
- 2-week timeframe is better for longer-term positions
- Combine with the Bull Market Support Band and key support/resistance levels
- Signals work best when price is at structural levels (as shown in your TradingView charts)

## Running with Claude Code

If you have Claude Code installed:
```bash
claude "run the leledc scanner on both timeframes and show me what's firing"
```
