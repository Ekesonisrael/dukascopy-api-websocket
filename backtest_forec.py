import yfinance as yf
import pandas as pd
import numpy as np

# ==========================================
# CONFIGURATION
# ==========================================
SYMBOLS = {
    "GC=F": "Gold Futures",    # XAUUSD proxy on Yahoo
    "EURUSD=X": "Euro/USD"
}
RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 1.5
FEE_RATE = 0.0001 # Spread equivalent (1 pip)

def run_backtest(ticker, name):
    print(f"\nFetching data for {name} ({ticker})...")
    
    # Fetch 15m data (yfinance limits 15m data to 60 days)
    df_15m = yf.download(ticker, period="60d", interval="15m", progress=False)
    df_4h = yf.download(ticker, period="60d", interval="1h", progress=False) # Use 1H to resample or just use for trend
    
    if df_15m.empty or df_4h.empty:
        print(f"Failed to fetch data for {ticker}")
        return

    # Flatten multi-index columns if necessary
    if isinstance(df_15m.columns, pd.MultiIndex): df_15m.columns = df_15m.columns.droplevel(1)
    if isinstance(df_4h.columns, pd.MultiIndex): df_4h.columns = df_4h.columns.droplevel(1)

    df_15m.reset_index(inplace=True)
    df_4h.reset_index(inplace=True)
    
    # Calculate 4H Trend (Using 1H resampled or just 1H as proxy if 4H missing)
    df_4h['ema20'] = df_4h['Close'].ewm(span=20, adjust=False).mean()
    df_4h['ema50'] = df_4h['Close'].ewm(span=50, adjust=False).mean()
    df_4h['trend'] = np.where(df_4h['ema20'] > df_4h['ema50'], 1, -1)

    # Calculate ATR and Vol MA on 15M
    df_15m['high_low'] = df_15m['High'] - df_15m['Low']
    df_15m['high_close'] = (df_15m['High'] - df_15m['Close'].shift()).abs()
    df_15m['low_close'] = (df_15m['Low'] - df_15m['Close'].shift()).abs()
    df_15m['tr'] = df_15m[['high_low', 'high_close', 'low_close']].max(axis=1)
    df_15m['atr'] = df_15m['tr'].rolling(window=14).mean()
    df_15m['vol_ma'] = df_15m['Volume'].rolling(window=20).mean()
    
    df_15m['recent_low'] = df_15m['Low'].rolling(window=20).min().shift(1)
    df_15m['recent_high'] = df_15m['High'].rolling(window=20).max().shift(1)

    # Merge Trend onto 15m data
    # Ensure datetime columns match types
    df_15m['Date'] = pd.to_datetime(df_15m['Datetime']).dt.tz_localize(None)
    df_4h['Date'] = pd.to_datetime(df_4h['Datetime']).dt.tz_localize(None)
    df = pd.merge_asof(df_15m, df_4h[['Date', 'trend']], on='Date', direction='backward')

    trades = []
    i = 20

    while i < len(df) - 2:
        sweep_row = df.iloc[i]
        conf_row = df.iloc[i+1]
        entry_row = df.iloc[i+2]

        trade_entered = False
        side = None

        # Sweep + Volume Filters
        swept_low = (sweep_row['Low'] < sweep_row['recent_low']) and (sweep_row['Close'] > sweep_row['recent_low']) and (sweep_row['Volume'] > sweep_row['vol_ma'] * VOLUME_MULTIPLIER)
        swept_high = (sweep_row['High'] > sweep_row['recent_high']) and (sweep_row['Close'] < sweep_row['recent_high']) and (sweep_row['Volume'] > sweep_row['vol_ma'] * VOLUME_MULTIPLIER)

        # Confirmation
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
            cost_r = FEE_RATE / (risk / entry) # Approx spread cost in R
            breakeven_triggered = False

            for j in range(i+2, len(df)):
                candle = df.iloc[j]
                
                if side == 'LONG':
                    if not breakeven_triggered and candle['High'] >= entry + risk:
                        sl = entry + (entry * FEE_RATE) # Move to BE + fees
                        breakeven_triggered = True
                    if candle['Low'] <= sl:
                        result = 'WIN' if breakeven_triggered else 'LOSS'
                        exit_idx = j; break
                    elif candle['High'] >= tp:
                        result, exit_idx = 'WIN', j; break
                        
                elif side == 'SHORT':
                    if not breakeven_triggered and candle['Low'] <= entry - risk:
                        sl = entry - (entry * FEE_RATE)
                        breakeven_triggered = True
                    if candle['High'] >= sl:
                        result = 'WIN' if breakeven_triggered else 'LOSS'
                        exit_idx = j; break
                    elif candle['Low'] <= tp:
                        result, exit_idx = 'WIN', j; break

            if exit_idx:
                if result == 'WIN':
                    net_r = RISK_REWARD if not breakeven_triggered else 0.0
                else:
                    net_r = -1.0
                
                trades.append({'result': result, 'r': net_r - cost_r})
                i = exit_idx + 1
            else:
                i += 1
        else:
            i += 1

    if not trades:
        print("No trades found.")
        return

    total = len(trades)
    wins = sum(1 for t in trades if t['r'] > 0)
    total_r = sum(t['r'] for t in trades)

    print(f"=== {name} BACKTEST RESULTS ===")
    print(f"Total Trades: {total} | Wins: {wins} | Win Rate: {(wins/total)*100:.2f}%")
    print(f"Total R: {total_r:+.2f}R | Avg R: {(total_r/total):+.2f}R")

if __name__ == "__main__":
    for ticker, name in SYMBOLS.items():
        run_backtest(ticker, name)
