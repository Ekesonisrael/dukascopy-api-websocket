import os
import pandas as pd
import json
from datetime import datetime
from tickerall import Tickerall

# ==========================================
# CONFIGURATION (XAU & USD ONLY)
# ==========================================
TICKERALL_API_KEY = os.getenv("TICKERALL_API_KEY")
MT5_SERVER = os.getenv("MT5_SERVER")
MT5_ACCOUNT = int(os.getenv("MT5_ACCOUNT"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Focusing on Gold and major USD pairs
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
RISK_PERCENTAGE = 0.03
JOURNAL_FILE = "forex_journal.json"

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e: print(f"Telegram Error: {e}")

def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r") as f: return json.load(f)
        except: return []
    return []

def save_journal(journal):
    with open(JOURNAL_FILE, "w") as f: json.dump(journal, f, indent=4)

def run_strategy():
    print(f"--- Starting Session at {datetime.now()} ---")
    
    # 1. Initialize TickerAll Client
    client = Tickerall(api_key=TICKERALL_API_KEY)
    
    try:
        # 2. Start Session
        session = client.sessions.start(
            broker="mt5",
            server=MT5_SERVER,
            account=MT5_ACCOUNT,
            password=MT5_PASSWORD,
            terminal_type="MOBILE"
        )
        account_id = session.account_id
        print(f"Connected successfully! TickerAll Account ID: {account_id}")
    except Exception as e:
        print(f"Failed to connect to MT5: {e}")
        send_telegram(f"⚠️ MT5 Connection Failed: {e}")
        return

    journal = load_journal()

    for symbol in SYMBOLS:
        # Guard: Skip if already in a trade
        if any(t['symbol'] == symbol and t['status'] == "OPEN" for t in journal):
            print(f"[GUARD] Open trade exists for {symbol}. Skipping.")
            continue

        # 3. Fetch Data (The FIX is here)
        try:
            # The SDK returns a list of candle objects directly, no need for ['candles']
            candles_4h = client.candles.get(account_id, symbol=symbol, count=50, timeframe="H4")
            candles_15m = client.candles.get(account_id, symbol=symbol, count=50, timeframe="M15")
            
            # Convert list of objects to DataFrame
            df_4h = pd.DataFrame([{
                'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close
            } for c in candles_4h])
            
            df_15m = pd.DataFrame([{
                'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close
            } for c in candles_15m])
            
        except Exception as e:
            print(f"Data error {symbol}: {e}")
            continue

        if df_4h.empty or df_15m.empty or len(df_4h) < 30:
            continue

        # 4. 4H Macro Trend
        df_4h['ema20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
        df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
        
        is_bullish = df_4h['ema20'].iloc[-1] > df_4h['ema50'].iloc[-1]
        is_bearish = df_4h['ema20'].iloc[-1] < df_4h['ema50'].iloc[-1]

        # 5. 15m Liquidity Sweep
        current_price = float(df_15m['close'].iloc[-1])
        recent_low = float(df_15m['low'].iloc[-20:-1].min())
        recent_high = float(df_15m['high'].iloc[-20:-1].max())

        swept_low = df_15m['low'].iloc[-1] < recent_low and current_price > recent_low
        swept_high = df_15m['high'].iloc[-1] > recent_high and current_price < recent_high

        signal = None
        if is_bullish and swept_low:
            signal = "LONG"
            sl = recent_low * 0.9995
            tp = current_price + (current_price - sl) * 2.5
        elif is_bearish and swept_high:
            signal = "SHORT"
            sl = recent_high * 1.0005
            tp = current_price - (sl - current_price) * 2.5
        else:
            print(f"[HOLD] {symbol}: No sweep setup.")
            continue

        # 6. Execute Trade via SDK
        print(f"🚨 SIGNAL: {signal} {symbol} | Entry: {current_price} | SL: {sl} | TP: {tp}")
        
        try:
            order = client.orders.place(
                account_id,
                type="market",
                symbol=symbol,
                side="BUY" if signal == "LONG" else "SELL",
                volume=0.01,
                stop_loss=sl,
                take_profit=tp
            )
            
            journal.append({
                "symbol": symbol, "side": signal, "entry": current_price,
                "sl": sl, "tp": tp, "status": "OPEN", "time": datetime.now().isoformat()
            })
            send_telegram(f"✅ {signal} {symbol}\nEntry: {current_price:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}\nTicket: {order.ticket}")
            
        except Exception as e:
            print(f"Order Failed: {e}")
            send_telegram(f"⚠️ Order Failed {symbol}: {e}")

    save_journal(journal)

if __name__ == "__main__":
    run_strategy()
