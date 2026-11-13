import os
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone
from tickerall import Tickerall

# ==========================================
# CONFIGURATION
# ==========================================
TICKERALL_API_KEY = os.getenv("TICKERALL_API_KEY")
MT5_SERVER = os.getenv("MT5_SERVER")
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")

SYMBOL = "XAUUSD"           
MONTHS_BACK = 12            

ATR_MULTIPLIER = 2.0        
TRAIL_ATR_MULTIPLIER = 3.0  
SPREAD = 0.20               # $0.20 spread on Gold

def run_backtest():
    print("Connecting to MT5 to fetch 1 year of 4H Gold data...", flush=True)
    
    client = Tickerall(api_key=TICKERALL_API_KEY, timeout=60)
    try:
        session = client.sessions.start(
            broker="mt5", server=MT5_SERVER, account=MT5_ACCOUNT,
            password=MT5_PASSWORD, terminal_type="MOBILE"
        )
        account_id = session.account_id
    except Exception as e:
        print(f"Failed to connect to MT5: {e}")
        return

    try:
        # Fetching 4H data
        candles = client.candles.get(account_id, symbol=SYMBOL, count=1000, timeframe="H4")
    except Exception as e:
        print(f"Failed to fetch candles: {e}")
        return

    if not candles:
        print("No data returned.")
        return

    print(f"Successfully fetched {len(candles)} candles.", flush=True)

    df = pd.DataFrame([{
        'time': c.time, 'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close, 'vol': getattr(c, 'volume', 1)
    } for c in candles])
    
    df['time'] = pd.to_datetime(df['time'])
    df = df.sort_values('time').reset_index(drop=True)

    # Calculate Indicators
    df['tr'] = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
    df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['vol_ma'] = df['vol'].rolling(20).mean()
    df['don_high'] = df['high'].rolling(20).max().shift(1)
    df['don_low'] = df['low'].rolling(20).min().shift(1)
    
    # ADX Calculation
    up = df['high'].diff()
    down = -df['low'].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr_s = df['tr'].ewm(alpha=1/14, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm).ewm(alpha=1/14, adjust=False).mean() / tr_s
    mdi = 100 * pd.Series(minus_dm).ewm(alpha=1/14, adjust=False).mean() / tr_s
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()

    trades = []
    tr = None
    
    for i in range(200, len(df)):
        candle = df.iloc[i]
        
        if tr:
            if tr['side'] == 'LONG':
                if candle['low'] <= tr['sl']:
                    pnl = (tr['sl'] - tr['entry'] - SPREAD)
                    trades.append(pnl)
                    tr = None
                else:
                    tr['sl'] = max(tr['sl'], candle['high'] - (TRAIL_ATR_MULTIPLIER * candle['atr']))
            else:
                if candle['high'] >= tr['sl']:
                    pnl = (tr['entry'] - tr['sl'] - SPREAD)
                    trades.append(pnl)
                    tr = None
                else:
                    tr['sl'] = min(tr['sl'], candle['low'] + (TRAIL_ATR_MULTIPLIER * candle['atr']))
                    
        if not tr:
            s = df.iloc[i-1]
            is_trending = s['adx'] > 20
            
            if is_trending and s['close'] > s['ema200'] and s['close'] > s['don_high']:
                entry = candle['open']
                sl = entry - (ATR_MULTIPLIER * s['atr'])
                tr = {'side': 'LONG', 'entry': entry, 'sl': sl}
            elif is_trending and s['close'] < s['ema200'] and s['close'] < s['don_low']:
                entry = candle['open']
                sl = entry + (ATR_MULTIPLIER * s['atr'])
                tr = {'side': 'SHORT', 'entry': entry, 'sl': sl}

    total = len(trades)
    if total == 0:
        print("No trades found.")
        return
        
    wins = sum(1 for r in trades if r > 0)
    total_profit = sum(trades)
    
    print("\n" + "="*50)
    print("  XAUUSD 4H TREND BACKTEST (1 YEAR)")
    print("="*50)
    print(f"  Trades: {total} | Win Rate: {(wins/total)*100:.1f}%")
    print(f"  Total Profit: ${total_profit:.2f}")
    print(f"  Avg Win: ${np.mean([t for t in trades if t > 0]):.2f}" if wins > 0 else "  Avg Win: $0.00")
    print(f"  Avg Loss: ${np.mean([t for t in trades if t <= 0]):.2f}" if (total-wins) > 0 else "  Avg Loss: $0.00")
    print("="*50)

if __name__ == "__main__":
    print("Starting FX 4H Trend Backtest...", flush=True)
    run_backtest()
