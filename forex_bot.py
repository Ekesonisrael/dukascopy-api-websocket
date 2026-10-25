import os
import requests
import pandas as pd
import json
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
TICKERALL_API_KEY = os.getenv("TICKERALL_API_KEY")
TICKERALL_ACCOUNT_ID = os.getenv("TICKERALL_ACCOUNT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# IF THE BOT SAYS "SYMBOL NOT FOUND", TRY ADDING "m" TO THESE (e.g., "XAUUSDm")
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
RISK_PERCENTAGE = 0.03
JOURNAL_FILE = "forex_journal.json"

BASE_URL = "https://api.tickerall.com/v1"
HEADERS = {
    "Authorization": f"Bearer {TICKERALL_API_KEY}", 
    "Content-Type": "application/json"
}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: 
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
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

def fetch_candles(symbol, timeframe, limit=50):
    """Fetches candles from TickerAll."""
    url = f"{BASE_URL}/accounts/{TICKERALL_ACCOUNT_ID}/candles"
    params = {"symbol": symbol, "timeframe": timeframe, "limit": limit}
    
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # Adjust this based on TickerAll's exact JSON response structure
            if 'candles' in data:
                return pd.DataFrame(data['candles'])
            elif isinstance(data, list):
                return pd.DataFrame(data)
        else:
            print(f"Data fetch error {symbol}: {r.text}")
    except Exception as e:
        print(f"Connection Error {symbol}: {e}")
    return None

def place_order(symbol, side, sl, tp):
    """Places a market order via TickerAll."""
    url = f"{BASE_URL}/accounts/{TICKERALL_ACCOUNT_ID}/orders"
    
    # We use a fixed volume for demo. 0.01 is standard micro lot.
    payload = {
        "symbol": symbol,
        "side": side, # "buy" or "sell"
        "type": "market",
        "volume": 0.01, 
        "sl": float(sl),
        "tp": float(tp)
    }
    
    try:
        r = requests.post(url, headers=HEADERS, json=payload, timeout=15)
        print(f"Order Response: {r.text}")
        return r.json()
    except Exception as e:
        print(f"Order Error: {e}")
        return None

def run_strategy():
    print(f"--- Scanning at {datetime.now()} ---")
    journal = load_journal()

    for symbol in SYMBOLS:
        # 1. Guard: Skip if we already have an open trade
        if any(t['symbol'] == symbol and t['status'] == "OPEN" for t in journal):
            print(f"[GUARD] Open trade exists for {symbol}. Skipping.")
            continue

        # 2. Fetch Data
        df_4h = fetch_candles(symbol, "4h")
        df_15m = fetch_candles(symbol, "15m")

        if df_4h is None or df_15m is None or len(df_4h) < 30 or len(df_15m) < 30:
            continue

        # 3. 4H Macro Trend
        df_4h['ema20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
        df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
        
        is_bullish = df_4h['ema20'].iloc[-1] > df_4h['ema50'].iloc[-1]
        is_bearish = df_4h['ema20'].iloc[-1] < df_4h['ema50'].iloc[-1]

        # 4. 15m Liquidity Sweep
        current_price = df_15m['close'].iloc[-1]
        recent_low = df_15m['low'].iloc[-20:-1].min()
        recent_high = df_15m['high'].iloc[-20:-1].max()

        swept_low = df_15m['low'].iloc[-1] < recent_low and current_price > recent_low
        swept_high = df_15m['high'].iloc[-1] > recent_high and current_price < recent_high

        signal = None
        
        if is_bullish and swept_low:
            signal = "LONG"
            side = "buy"
            sl = recent_low * 0.9995
            tp = current_price + (current_price - sl) * 2.5
        elif is_bearish and swept_high:
            signal = "SHORT"
            side = "sell"
            sl = recent_high * 1.0005
            tp = current_price - (sl - current_price) * 2.5
        else:
            print(f"[HOLD] {symbol}: No sweep setup.")
            continue

        # 5. Execute Trade
        print(f"🚨 SIGNAL: {signal} {symbol} | Entry: {current_price} | SL: {sl} | TP: {tp}")
        
        result = place_order(symbol, side, sl, tp)

        if result and result.get("ticket"): # Assuming TickerAll returns a ticket ID
            journal.append({
                "symbol": symbol, "side": side, "entry": current_price,
                "sl": sl, "tp": tp, "status": "OPEN", "time": datetime.now().isoformat()
            })
            send_telegram(f"✅ {signal} {symbol}\nEntry: {current_price:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}")
        else:
            print(f"Order Failed: {result}")
            send_telegram(f"⚠️ Order Failed on {symbol}")

    save_journal(journal)

if __name__ == "__main__":
    run_strategy()
