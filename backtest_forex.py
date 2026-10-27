import yfinance as yf
import pandas as pd
import numpy as np

# ==========================================
# CONFIGURATION
# ==========================================
SYMBOLS = {
    "GC=F": "Gold Futures" # Only testing Gold
}
RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
FEE_RATE = 0.0001 

TRADING_HOURS_START = 13
TRADING_HOURS_END = 17

# ✅ ADDED: List of time periods to test
PERIODS = ["30d", "60d", "3mo", "6mo", "1y"]

def run_backtest(ticker, name, period):
    print(f"\nFetching {period} of data for {name} ({ticker})...")
    
    # ✅ CHANGED: We must use 1h data to go past 60 days
    df = yf.download(ticker, period=period, interval="1h", progress=False)
    
    if df.empty:
        print(f"Failed to fetch data for {ticker} ({period})")
        return

    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
    df.reset_index(inplace=True)
    
    df['Datetime'] = pd.to_datetime(df['Datetime'])
    df.set_index('Datetime', inplace=True)
    
    # Resample to 4H for the trend
    df_4h = df.resample('4h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'}).dropna()
    df_4h['ema20'] = df_4h['Close'].ewm(span=20, adjust=False).mean()
    df_4h['ema50'] = df_4h['Close'].ewm(span=50, adjust=False).mean()
    df_4h['trend'] = np.where(df_4h['ema20'] > df_4h['ema50'], 1, -1)
    
    df.reset_index(inplace=True)
    
    # Calculate ATR on 1H
    df['high_low'] = df['High'] - df['Low']
    df['high_close'] = (df['High'] - df['Close'].shift()).abs()
    df['low_close'] = (df['Low'] - df['Close'].shift()).abs()
    df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=14).mean()
    
    df['recent_low'] = df['Low'].rolling(window=20).min().shift(1)
    df['recent_high'] = df['High'].rolling(window=20).max().shift(1)

    df['4h_time'] = df['Datetime'].dt.floor('4h')
    df = pd.merge_asof(df, df_4h[['trend']], left_on='4h_time', right_index=True, direction='backward')

    trades = []
    i = 50 # Start at 50 to ensure EMA is ready

    while i < len(df) - 2:
        sweep_row = df.iloc[i]
        conf_row = df.iloc[i+1]
        entry_row = df.iloc[i+2]

        current_hour = sweep_row['Datetime'].hour
        if not (TRADING_HOURS_START <= current_hour < TRADING_HOURS_END):
            i += 1
            continue

        trade_entered = False
        side = None

        swept_low = (sweep_row['Low'] < sweep_row['recent_low']) and (sweep_row['Close'] > sweep_row['recent_low'])
        swept_high = (sweep_row['High'] > sweep_row['recent_high']) and (sweep_row['Close'] < sweep_row['recent_high'])

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

    if not trades:
        print(f"=== {name} ({period}) === No trades found.")
        return

    total = len(trades)
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    total_r = sum(t['r'] for t in trades)

    print(f"=== {name} ({period} BACKTEST) ===")
    print(f"Total Trades: {total} | Wins: {wins} | Win Rate: {(wins/total)*100:.2f}%")
    print(f"Total R: {total_r:+.2f}R | Avg R: {(total_r/total):+.2f}R")

if __name__ == "__main__":
    for ticker, name in SYMBOLS.items():
        for period in PERIODS:
            run_backtest(ticker, name, period)
