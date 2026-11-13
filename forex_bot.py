import os
import pandas as pd
import numpy as np 
import json
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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ✅ EXNESS MICRO GOLD
SYMBOL = "XAUUSDm"
JOURNAL_FILE = "forex_journal.json"

RISK_PERCENTAGE = 0.015      # 1.5% Risk per trade
ATR_MULTIPLIER = 2.0         # Initial Stop Loss
TRAIL_ATR_MULTIPLIER = 3.0   # Trailing Stop

def now_utc():
    return datetime.now(timezone.utc)

def send_telegram(msg, markdown=True):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    if markdown:
        payload["parse_mode"] = "Markdown"
    try: 
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok and markdown:
            send_telegram(msg, markdown=False)
    except Exception as e: 
        print(f"Telegram Error: {e}")

def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r") as f: return json.load(f)
        except: return []
    return []

def save_journal(journal):
    with open(JOURNAL_FILE, "w") as f: json.dump(journal, f, indent=4)

def fetch_mt5_candles(client, account_id, symbol, timeframe, count):
    try:
        candles = client.candles.get(account_id, symbol=symbol, count=count, timeframe=timeframe)
        if not candles: return pd.DataFrame()
        
        df = pd.DataFrame([{
            'time': c.timestamp, 
            'open': c.open, 
            'high': c.high, 
            'low': c.low, 
            'close': c.close, 
            'vol': getattr(c, 'volume', getattr(c, 'tick_volume', 1))
        } for c in candles])
        
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        return df.sort_values('time').reset_index(drop=True)
    except Exception as e:
        print(f"Fetch error: {e}")
        return pd.DataFrame()

def manage_open_trade(client, account_id, journal):
    open_trades = [t for t in journal if t['status'] == "OPEN"]
    if not open_trades:
        return False
        
    trade = open_trades[0] # We only hold 1 trade at a time
    
    try:
        # Get current price
        ticker = client.ticker.get(account_id, symbol=trade['symbol'])
        current_price = float(ticker.ask if trade['side'] == 'BUY' else ticker.bid)
        
        # Get current 4H ATR
        df = fetch_mt5_candles(client, account_id, trade['symbol'], "H4", 50)
        if df.empty: return False
        current_atr = df['tr'].ewm(alpha=1/14, adjust=False).mean().iloc[-1]
        
        # Check for trailing stop update
        if trade['side'] == 'BUY':
            trail_sl = current_price - (current_atr * TRAIL_ATR_MULTIPLIER)
            if trail_sl > trade['sl']:
                trade['sl'] = trail_sl
                save_journal(journal)
                print(f"Trailing SL updated for {trade['symbol']} to {trail_sl:.2f}")
                
                # Update stop loss on MT5
                # Note: MT5 API for modifying orders can vary. If your Tickerall supports it, add it here.
                # client.orders.modify(account_id, ticket=trade['ticket'], stop_loss=trail_sl)
                
            if current_price <= trade['sl']:
                close_trade(client, account_id, journal, trade, current_price, "Trailing Stop Hit")
                return True
        else:
            trail_sl = current_price + (current_atr * TRAIL_ATR_MULTIPLIER)
            if trail_sl < trade['sl']:
                trade['sl'] = trail_sl
                save_journal(journal)
                print(f"Trailing SL updated for {trade['symbol']} to {trail_sl:.2f}")
                
            if current_price >= trade['sl']:
                close_trade(client, account_id, journal, trade, current_price, "Trailing Stop Hit")
                return True
                
    except Exception as e:
        print(f"Error managing trade: {e}")
        
    return False

def close_trade(client, account_id, journal, trade, exit_price, reason):
    try:
        # In a real MT5 integration, you would send a close order here.
        # For now, we just mark it closed in the journal.
        trade['status'] = 'CLOSED'
        trade['exit'] = exit_price
        trade['close_reason'] = reason
        trade['closed_at'] = now_utc().isoformat()
        save_journal(journal)
        send_telegram(f"🛑 *TRADE CLOSED* ({reason})\nAsset: {trade['symbol']}\nEntry: {trade['entry']:.2f}\nExit: {exit_price:.2f}")
    except Exception as e:
        print(f"Close error: {e}")

def run_strategy():
    try:
        print(f"--- FX 4H Scan at {now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC ---")
        
        client = Tickerall(api_key=TICKERALL_API_KEY, timeout=60)
        
        try:
            session = client.sessions.start(
                broker="mt5", server=MT5_SERVER, account=MT5_ACCOUNT,
                password=MT5_PASSWORD, terminal_type="MOBILE"
            )
            account_id = session.account_id
        except Exception as e:
            print(f"Failed to connect: {e}")
            return

        journal = load_journal()
        
        # 1. Manage existing trade
        if manage_open_trade(client, account_id, journal):
            return # If we just closed a trade, wait for next run

        # 2. Check for new entries
        if any(t['status'] == "OPEN" for t in journal):
            print("[SKIP] Trade already open.")
            return

        df = fetch_mt5_candles(client, account_id, SYMBOL, "H4", 250)
        if df.empty or len(df) < 200:
            print("[SKIP] Not enough data.")
            return

        # Indicators
        df['tr'] = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
        df['atr'] = df['tr'].ewm(alpha=1/14, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['don_high'] = df['high'].rolling(20).max().shift(1)
        df['don_low'] = df['low'].rolling(20).min().shift(1)
        
        up = df['high'].diff()
        down = -df['low'].diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        tr_s = df['tr'].ewm(alpha=1/14, adjust=False).mean()
        pdi = 100 * pd.Series(plus_dm).ewm(alpha=1/14, adjust=False).mean() / tr_s
        mdi = 100 * pd.Series(minus_dm).ewm(alpha=1/14, adjust=False).mean() / tr_s
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
        df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()

        # Use the last closed candle
        last = df.iloc[-1]
        
        is_trending = last['adx'] > 20
        is_bullish = last['close'] > last['ema200']
        is_bearish = last['close'] < last['ema200']
        
        signal = None
        if is_trending and is_bullish and last['close'] > last['don_high']:
            signal = "LONG"
        elif is_trending and is_bearish and last['close'] < last['don_low']:
            signal = "SHORT"

        if signal:
            ticker = client.ticker.get(account_id, symbol=SYMBOL)
            entry_price = float(ticker.ask if signal == "LONG" else ticker.bid)
            atr = last['atr']
            
            if signal == "LONG":
                sl = entry_price - (atr * ATR_MULTIPLIER)
            else:
                sl = entry_price + (atr * ATR_MULTIPLIER)

            # Position Sizing
            # Fetch account balance (Assuming Tickerall provides this)
            # For demo purposes, if balance isn't available, assume $10,000
            try:
                balance_info = client.accounts.get(account_id)
                balance = float(balance_info.balance)
            except:
                balance = 10000.0
                
            risk_amount = balance * RISK_PERCENTAGE
            price_risk = abs(entry_price - sl)
            
            # Calculate lot size (Gold: 1 lot = 100 oz. 1 pip = $0.10)
            # For XAUUSDm, standard lot = 0.01 of standard.
            # This requires knowing the exact contract specs from Exness.
            # We will use a rough estimate: volume = risk / (price_risk * 100)
            volume = round((risk_amount / price_risk) / 100, 2)
            if volume < 0.01: volume = 0.01 # Minimum lot size

            print(f"🚨 {signal} SIGNAL on {SYMBOL} | Entry: {entry_price:.2f} | SL: {sl:.2f} | Vol: {volume}")
            
            try:
                order = client.orders.place(
                    account_id, type="market", symbol=SYMBOL, side="BUY" if signal == "LONG" else "SELL",
                    volume=volume, stop_loss=sl
                )
                
                journal.append({
                    "symbol": SYMBOL, "side": "BUY" if signal == "LONG" else "SELL", 
                    "entry": entry_price, "sl": sl, "amount": volume,
                    "status": "OPEN", "time": now_utc().isoformat(), "ticket": order.ticket
                })
                save_journal(journal)
                
                send_telegram(f"✅ *{signal} EXECUTED*\nAsset: {SYMBOL}\nEntry: {entry_price:.2f}\nSL: {sl:.2f}\nVol: {volume}")
            except Exception as e:
                print(f"Order Failed: {e}")
                send_telegram(f"⚠️ Order Failed {SYMBOL}: {e}", markdown=False)
        else:
            print(f"[HOLD] {SYMBOL}: No setup. ADX={last['adx']:.1f}, Close={last['close']:.2f}")

    except Exception as e:
        print(f"[FATAL ERROR] {e}")

if __name__ == "__main__":
    run_strategy()
