import os
import pandas as pd
import numpy as np
from datetime import datetime
from tickerall import Tickerall

# ==========================================
# CONFIGURATION
# ==========================================
TICKERALL_API_KEY = os.getenv("TICKERALL_API_KEY")
MT5_SERVER = os.getenv("MT5_SERVER")
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")

SYMBOL = "XAUUSD"
RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 1.5
FEE_RATE = 0.0001

TRADING_HOURS_START = 13
TRADING_HOURS_END = 17

def run_backtest():
    print("Connecting to MT5...")
    
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

    # Try 200 days, then 90 days, then 30 days to prevent empty data
    lookbacks = [4800, 2160, 720] 
    candles_15m = []
    
    for hours in lookbacks:
        print(f"Attempting to fetch {hours} hours (approx {hours//24} days) of 15m data...")
        try:
            candles_15m = client.candles.get(
                account_id, symbol=SYMBOL, hours=hours, timeframe="M15"
            )
            if candles_15m:
                print(f"✅ Successfully fetched {len(candles_15m)} candles!")
                break
        except Exception as e:
            print(f"Fetch failed for {hours} hours: {e}")

    if not candles_15m:
        print("❌ No data returned. Your broker might not provide M15 history via API.")
        return

    print("Processing data and running backtest...")

    # Build DataFrame
    df = pd.DataFrame([{
        'time': c.time, 'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close, 'vol': getattr(c, 'volume', 1)
    } for c in candles_15m])

    df['time'] = pd.to_datetime(df['time'])
    df.set_index('time', inplace=True)

    # Resample to 4H for trend
    df_4h = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    df_4h['ema20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
    df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
    df_4h['trend'] = np.where(df_4h['ema20'] > df_4h['ema50'], 1, -1)

    df.reset_index(inplace=True)

    # Calculate ATR and Vol MA on 15M
    df['high_low'] = df['high'] - df['low']
    df['high_close'] = (df['high'] - df['close'].shift()).abs()
    df['low_close'] = (df['low'] - df['close'].shift()).abs()
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    df['vol_ma'] = df['vol'].rolling(window=20).mean()

    df['recent_low'] = df['low'].rolling(window=20).min().shift(1)
    df['recent_high'] = df['high'].rolling(window=20).max().shift(1)

    # Merge 4H Trend
    df['4h_time'] = df['time'].dt.floor('4h')
    df = pd.merge_asof(df, df_4h[['trend']], left_on='4h_time', right_index=True, direction='backward')

    trades = []
    i = 50 

    # The Backtest Loop
    while i < len(df) - 2:
        sweep_row = df.iloc[i]
        conf_row = df.iloc[i+1]
        entry_row = df.iloc[i+2]

        # Session Filter
        current_hour = sweep_row['time'].hour
        if not (TRADING_HOURS_START <= current_hour < TRADING_HOURS_END):
            i += 1
            continue

        trade_entered = False
        side = None

        swept_low = (sweep_row['low'] < sweep_row['recent_low']) and (sweep_row['close'] > sweep_row['recent_low']) and (sweep_row['vol'] > sweep_row['vol_ma'] * VOLUME_MULTIPLIER)
        swept_high = (sweep_row['high'] > sweep_row['recent_high']) and (sweep_row['close'] < sweep_row['recent_high']) and (sweep_row['vol'] > sweep_row['vol_ma'] * VOLUME_MULTIPLIER)

        conf_long = conf_row['close'] > conf_row['open']
        conf_short = conf_row['close'] < conf_row['open']

        if sweep_row['trend'] == 1 and swept_low and conf_long:
            entry = entry_row['open']
            sl = entry - (sweep_row['atr'] * ATR_MULTIPLIER)
            risk = entry - sl
            tp = entry + (risk * RISK_REWARD)
            trade_entered = True
            side = 'LONG'
            
        elif sweep_row['trend'] == -1 and swept_high and conf_short:
            entry = entry_row['open']
            sl = entry + (sweep_row['atr'] * ATR_MULTIPLIER)
            risk = sl - entry
            tp = entry - (risk * RISK_REWARD)
            trade_entered = True
            side = 'SHORT'

        if trade_entered:
            exit_idx = None
            result = None
            cost_r = FEE_RATE / (risk / entry) 

            for j in range(i+2, len(df)):
                candle = df.iloc[j]
                
                if side == 'LONG':
                    if candle['low'] <= sl:
                        result = 'LOSS'
                        exit_idx = j; break
                    elif candle['high'] >= tp:
                        result = 'WIN'
                        exit_idx = j; break
                        
                elif side == 'SHORT':
                    if candle['high'] >= sl:
                        result = 'LOSS'
                        exit_idx = j; break
                    elif candle['low'] <= tp:
                        result = 'WIN'
                        exit_idx = j; break

            if exit_idx:
                net_r = (RISK_REWARD if result == 'WIN' else -1.0) - cost_r
                trades.append({'result': result, 'r': net_r})
                i = exit_idx + 1
            else:
                i += 1
        else:
            i += 1

    if not trades:
        print("No trades found in this data period.")
        return

    total = len(trades)
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    total_r = sum(t['r'] for t in trades)

    print(f"\n=========================================")
    print(f"=== {SYMBOL} BACKTEST RESULTS ===")
    print(f"=========================================")
    print(f"Total Trades: {total}")
    print(f"Wins: {wins} | Win Rate: {(wins/total)*100:.2f}%")
    print(f"Total R: {total_r:+.2f}R | Avg R: {(total_r/total):+.2f}R")

if __name__ == "__main__":
    run_backtest()
