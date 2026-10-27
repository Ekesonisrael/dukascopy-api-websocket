import pandas as pd
import numpy as np
import yfinance as yf

# ==========================================
# CONFIGURATION (GOLD ONLY)
# ==========================================
SYMBOLS = {"GC=F": "Gold"}
RISK_REWARD = 2.0       # ✅ Back to 2.0
ATR_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 1.5
FEE_RATE = 0.0010       # ✅ Keeping the Brutal 0.1% fee
TRADING_HOURS_START = 13
TRADING_HOURS_END = 17

def run_backtest(ticker, name, split=False):
    df = yf.download(ticker, period="60d", interval="15m", progress=False)
    if df.empty: return
    
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
    df.reset_index(inplace=True)
    df['Datetime'] = pd.to_datetime(df['Datetime'], utc=True)
    df.set_index('Datetime', inplace=True)

    df_4h = df.resample('4h').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'}).dropna()
    df_4h['ema20'] = df_4h['Close'].ewm(span=20, adjust=False).mean()
    df_4h['ema50'] = df_4h['Close'].ewm(span=50, adjust=False).mean()
    df_4h['trend'] = np.where(df_4h['ema20'] > df_4h['ema50'], 1, -1)
    
    df.reset_index(inplace=True)
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

    if split:
        mid_point = len(df) // 2
        df = df.iloc[mid_point:].reset_index(drop=True) 
        print(f"\n--- {name} (OUT-OF-SAMPLE: Last 30 Days) ---")
    else:
        print(f"\n--- {name} (IN-SAMPLE: Full 60 Days) ---")

    trades = []
    i = 50 

    while i < len(df) - 2:
        sweep_row = df.iloc[i]
        conf_row = df.iloc[i+1]
        entry_row = df.iloc[i+2]

        if not (TRADING_HOURS_START <= sweep_row['Datetime'].hour < TRADING_HOURS_END):
            i += 1
            continue

        swept_low = (sweep_row['Low'] < sweep_row['recent_low']) and (sweep_row['Close'] > sweep_row['recent_low']) and (sweep_row['Volume'] > sweep_row['vol_ma'] * VOLUME_MULTIPLIER)
        swept_high = (sweep_row['High'] > sweep_row['recent_high']) and (sweep_row['Close'] < sweep_row['recent_high']) and (sweep_row['Volume'] > sweep_row['vol_ma'] * VOLUME_MULTIPLIER)

        conf_long = conf_row['Close'] > conf_row['Open']
        conf_short = conf_row['Close'] < conf_row['Open']

        side = None
        if sweep_row['trend'] == 1 and swept_low and conf_long:
            side = 'LONG'
        elif sweep_row['trend'] == -1 and swept_high and conf_short:
            side = 'SHORT'

        if side:
            entry = entry_row['Open']
            sl = entry - (sweep_row['atr'] * ATR_MULTIPLIER) if side == 'LONG' else entry + (sweep_row['atr'] * ATR_MULTIPLIER)
            risk = abs(entry - sl)
            tp = entry + (risk * RISK_REWARD) if side == 'LONG' else entry - (risk * RISK_REWARD)
            
            cost_r = FEE_RATE / (risk / entry) 

            # ✅ NO BREAK-EVEN LOGIC. Pure SL and TP.
            for j in range(i+2, len(df)):
                candle = df.iloc[j]
                
                if side == 'LONG':
                    if candle['Low'] <= sl:
                        trades.append(-1.0 - cost_r)
                        i = j; break
                    elif candle['High'] >= tp:
                        trades.append(RISK_REWARD - cost_r)
                        i = j; break
                        
                elif side == 'SHORT':
                    if candle['High'] >= sl:
                        trades.append(-1.0 - cost_r)
                        i = j; break
                    elif candle['Low'] <= tp:
                        trades.append(RISK_REWARD - cost_r)
                        i = j; break
            else: i += 1
        else: i += 1

    if not trades: return

    total_trades = len(trades)
    wins = sum(1 for r in trades if r > 0)
    total_r = sum(trades)
    
    cumulative_r = np.cumsum(trades)
    running_max = np.maximum.accumulate(cumulative_r)
    drawdown = cumulative_r - running_max
    max_dd = np.min(drawdown) if len(drawdown) > 0 else 0

    print(f"Trades: {total_trades} | Win Rate: {(wins/total_trades)*100:.1f}%")
    print(f"Total R: {total_r:+.2f}R | Max Drawdown: {max_dd:.2f}R")

if __name__ == "__main__":
    for ticker, name in SYMBOLS.items():
        run_backtest(ticker, name, split=False)
        run_backtest(ticker, name, split=True)
