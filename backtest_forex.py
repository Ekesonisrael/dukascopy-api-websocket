import pandas as pd
import numpy as np
import yfinance as yf

# ==========================================
# CONFIGURATION
# ==========================================
SYMBOLS = {"GC=F": "Gold Futures"}
RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 1.5 # Keeping this, but we will bypass it if Yahoo sends 0s
FEE_RATE = 0.0001 

TRADING_HOURS_START = 13
TRADING_HOURS_END = 17

PERIOD = "30d"
INTERVAL = "15m" 

def run_backtest(ticker, name):
    print(f"\nFetching {PERIOD} of {INTERVAL} data for {name} ({ticker})...")
    
    df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False)
    
    if df.empty:
        print(f"❌ Failed to fetch data for {ticker}.")
        return

    if isinstance(df.columns, pd.MultiIndex): 
        df.columns = df.columns.droplevel(1)
    df.reset_index(inplace=True)
    
    # ✅ FIX 1: Force everything to UTC
    df['Datetime'] = pd.to_datetime(df['Datetime'], utc=True)
    
    # ✅ FIX 2: Yahoo sometimes sends 0 volume for Gold. We replace 0 with 1 so the bot doesn't crash the filter.
    df['Volume'] = df['Volume'].replace(0, 1)
    df['Volume'] = df['Volume'].fillna(1)

    df.set_index('Datetime', inplace=True)

    # Resample to 4H
    df_4h = df.resample('4h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'}).dropna()
    df_4h['ema20'] = df_4h['Close'].ewm(span=20, adjust=False).mean()
    df_4h['ema50'] = df_4h['Close'].ewm(span=50, adjust=False).mean()
    df_4h['trend'] = np.where(df_4h['ema20'] > df_4h['ema50'], 1, -1)
    
    df.reset_index(inplace=True)

    # ATR and Volume MA
    df['high_low'] = df['High'] - df['Low']
    df['high_close'] = (df['High'] - df['Close'].shift()).abs()
    df['low_close'] = (df['Low'] - df['Close'].shift()).abs()
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    df['vol_ma'] = df['Volume'].rolling(window=20).mean()

    df['recent_low'] = df['Low'].rolling(window=20).min().shift(1)
    df['recent_high'] = df['High'].rolling(window=20).max().shift(1)

    df['4h_time'] = df['Datetime'].dt.floor('4h')
    df = pd.merge_asof(df, df_4h[['trend']], left_on='4h_time', right_index=True, direction='backward')

    trades = []
    i = 50 
    
    # ✅ Debug counters
    sweeps_found = 0
    sessions_passed = 0

    while i < len(df) - 2:
        sweep_row = df.iloc[i]
        conf_row = df.iloc[i+1]
        entry_row = df.iloc[i+2]

        # Session Filter
        current_hour = sweep_row['Datetime'].hour
        if not (TRADING_HOURS_START <= current_hour < TRADING_HOURS_END):
            i += 1
            continue
        
        sessions_passed += 1

        trade_entered = False
        side = None

        # To debug, let's remove the volume filter temporarily if Yahoo is giving us garbage data
        vol_condition_low = (sweep_row['Volume'] >= sweep_row['vol_ma'] * VOLUME_MULTIPLIER) if sweep_row['vol_ma'] > 1 else True
        vol_condition_high = (sweep_row['Volume'] >= sweep_row['vol_ma'] * VOLUME_MULTIPLIER) if sweep_row['vol_ma'] > 1 else True

        swept_low = (sweep_row['Low'] < sweep_row['recent_low']) and (sweep_row['Close'] > sweep_row['recent_low']) and vol_condition_low
        swept_high = (sweep_row['High'] > sweep_row['recent_high']) and (sweep_row['Close'] < sweep_row['recent_high']) and vol_condition_high

        if swept_low or swept_high:
            sweeps_found += 1

        conf_long = conf_row['Close'] > conf_row['Open']
        conf_short = conf_row['Close'] < conf_row['Open']

        if sweep_row['trend'] == 1 and swept_low and conf_long:
            entry = entry_row['Open']
            sl = entry - (sweep_row['atr'] * ATR_MULTIPLIER)
            risk = entry - sl
            tp = entry + (risk * RISK_REWARD)
            trade_entered = True
            side = 'LONG'
            
        elif sweep_row['trend'] == -1 and swept_high and conf_short:
            entry = entry_row['Open']
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
                    if candle['Low'] <= sl:
                        result = 'LOSS'
                        exit_idx = j; break
                    elif candle['High'] >= tp:
                        result = 'WIN'
                        exit_idx = j; break
                        
                elif side == 'SHORT':
                    if candle['High'] >= sl:
                        result = 'LOSS'
                        exit_idx = j; break
                    elif candle['Low'] <= tp:
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

    print(f"--- DEBUG INFO ---")
    print(f"Candles in Session: {sessions_passed}")
    print(f"Raw Sweeps Found: {sweeps_found}")

    if not trades:
        print("❌ No trades executed. Sweeps happened, but trend/confirmation failed.")
        return

    total = len(trades)
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    total_r = sum(t['r'] for t in trades)

    print(f"\n=== {name} 30-DAY BACKTEST (15m) ===")
    print(f"Total Trades: {total} | Wins: {wins} | Win Rate: {(wins/total)*100:.2f}%")
    print(f"Total R: {total_r:+.2f}R | Avg R: {(total_r/total):+.2f}R")

if __name__ == "__main__":
    for ticker, name in SYMBOLS.items():
        run_backtest(ticker, name)
