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

# ✅ ONLY GOLD
SYMBOLS = ["XAUUSD", "XAUUSDm"]
JOURNAL_FILE = "forex_journal.json"

RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 0.1 # Aggressive mode: volume filter off

# ✅ AGGRESSIVE MODE: 24-Hour Trading
TRADING_HOURS_START = 0
TRADING_HOURS_END = 24

# Global variable for debug cooldown
last_forex_telegram_time = 0

def is_market_open():
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    if weekday == 4 and now.hour >= 21: return False # Friday
    if weekday == 5: return False # Saturday
    if weekday == 6 and now.hour < 21: return False # Sunday
    return True

def send_telegram(msg, markdown=True):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    if markdown:
        payload["parse_mode"] = "Markdown"
    try: 
        r = requests.post(url, json=payload, timeout=10)
        # If Markdown parsing fails, resend as plain text
        if not r.ok and markdown:
            send_telegram(msg, markdown=False)
    except Exception as e: 
        print(f"Telegram Error: {e}")

def get_pip_size(symbol):
    if "JPY" in symbol: return 0.01
    if "XAU" in symbol: return 0.1 
    return 0.0001

def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r") as f: return json.load(f)
        except: return []
    return []

def save_journal(journal):
    with open(JOURNAL_FILE, "w") as f: json.dump(journal, f, indent=4)

def check_closed_trades(client, account_id, journal):
    updated = False
    try:
        # ✅ FIXED: Use accounts.get to fetch positions
        detail = client.accounts.get(account_id)
        open_positions = detail.positions if hasattr(detail, 'positions') else []
        open_symbols = [p.symbol for p in open_positions]
        
        for trade in journal:
            if trade['status'] == "OPEN":
                if trade['symbol'] not in open_symbols:
                    report = (
                        f"🔔 *TRADE CLOSED: {trade['symbol']}*\n\n"
                        f"Check your Exness app for the final PnL.\n"
                        f"Entry was: {trade['entry']:.5f}\n"
                        f"🧠 Strategy: ATR Sweep"
                    )
                    send_telegram(report)
                    print(report)
                    
                    trade['status'] = "CLOSED"
                    updated = True
    except Exception as e:
        error_msg = f"⚠️ [RESOLVER ERROR] Could not check closed trades:\n{e}"
        print(error_msg)
        send_telegram(error_msg, markdown=False) # Send error to Telegram
        
    return updated

def run_strategy():
    global last_forex_telegram_time
    
    try:
        if not is_market_open():
            print(f"--- Market is CLOSED (Weekend). Sleeping... ---")
            return 

        current_utc_hour = datetime.now(timezone.utc).hour
        if not (TRADING_HOURS_START <= current_utc_hour < TRADING_HOURS_END):
            return

        print(f"--- Session OPEN. Starting Scan at {datetime.now()} ---")
        
        client = Tickerall(api_key=TICKERALL_API_KEY, timeout=60)
        
        try:
            session = client.sessions.start(
                broker="mt5", server=MT5_SERVER, account=MT5_ACCOUNT,
                password=MT5_PASSWORD, terminal_type="MOBILE"
            )
            account_id = session.account_id
        except Exception as e:
            error_msg = f"⚠️ [CONNECTION ERROR] Failed to connect to MT5:\n{e}"
            print(error_msg)
            send_telegram(error_msg, markdown=False)
            return

        journal = load_journal()

        if check_closed_trades(client, account_id, journal):
            save_journal(journal)

        for symbol in SYMBOLS:
            if any(t['symbol'] == symbol and t['status'] == "OPEN" for t in journal):
                continue

            print(f"Fetching data for {symbol}...")
            df_4h = pd.DataFrame()
            df_15m = pd.DataFrame()
            
            for attempt in range(2):
                try:
                    candles_4h = client.candles.get(account_id, symbol=symbol, count=50, timeframe="H4")
                    candles_15m = client.candles.get(account_id, symbol=symbol, count=50, timeframe="M15")
                    df_4h = pd.DataFrame([{'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close, 'vol': getattr(c, 'volume', 1)} for c in candles_4h])
                    df_15m = pd.DataFrame([{'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close, 'vol': getattr(c, 'volume', 1)} for c in candles_15m])
                    break 
                except Exception as e:
                    if attempt == 0: time.sleep(1) 
                    else:
                        print(f"⚠️ [DATA ERROR] Failed to fetch candles for {symbol}: {e}")

            if df_4h.empty or df_15m.empty or len(df_4h) < 30:
                continue 

            # 4H Trend
            df_4h['ema20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
            df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
            is_bullish = df_4h['ema20'].iloc[-1] > df_4h['ema50'].iloc[-1]
            is_bearish = df_4h['ema20'].iloc[-1] < df_4h['ema50'].iloc[-1]

            # ATR and Volume MA
            df_15m['high_low'] = df_15m['high'] - df_15m['low']
            df_15m['high_close'] = (df_15m['high'] - df_15m['close'].shift()).abs()
            df_15m['low_close'] = (df_15m['low'] - df_15m['close'].shift()).abs()
            df_15m['tr'] = df_15m[['high_low', 'high_close', 'low_close']].max(axis=1)
            df_15m['atr'] = df_15m['tr'].rolling(window=14).mean()
            df_15m['vol_ma'] = df_15m['vol'].rolling(window=20).mean()

            sweep_candle = df_15m.iloc[-2]
            conf_candle = df_15m.iloc[-1]
            
            recent_low = float(df_15m['low'].iloc[-20:-2].min())
            recent_high = float(df_15m['high'].iloc[-20:-2].max())

            # Volume fallback
            vol_ok_low = (sweep_candle['vol'] > sweep_candle['vol_ma'] * VOLUME_MULTIPLIER) if sweep_candle['vol_ma'] > 0 else True
            vol_ok_high = (sweep_candle['vol'] > sweep_candle['vol_ma'] * VOLUME_MULTIPLIER) if sweep_candle['vol_ma'] > 0 else True

            swept_low = (sweep_candle['low'] < recent_low) and (sweep_candle['close'] > recent_low) and vol_ok_low
            swept_high = (sweep_candle['high'] > recent_high) and (sweep_candle['close'] < recent_high) and vol_ok_high

            confirm_long = conf_candle['close'] > conf_candle['open']
            confirm_short = conf_candle['close'] < conf_candle['open']

            current_price = float(df_15m['close'].iloc[-1])
            atr_value = float(sweep_candle['atr'])

            signal = None
            if is_bullish and swept_low and confirm_long:
                signal = "LONG"
                sl = current_price - (atr_value * ATR_MULTIPLIER)
                risk = current_price - sl
                tp = current_price + (risk * RISK_REWARD)
            elif is_bearish and swept_high and confirm_short:
                signal = "SHORT"
                sl = current_price + (atr_value * ATR_MULTIPLIER)
                risk = sl - current_price
                tp = current_price - (risk * RISK_REWARD)

            if signal:
                print(f"🚨 SIGNAL: {signal} {symbol} | Entry: {current_price:.5f} | SL: {sl:.5f} | TP: {tp:.5f}")
                
                try:
                    order = client.orders.place(
                        account_id, type="market", symbol=symbol, side="BUY" if signal == "LONG" else "SELL",
                        volume=0.01, stop_loss=sl, take_profit=tp
                    )
                    
                    pip_size = get_pip_size(symbol)
                    sl_pips = abs(current_price - sl) / pip_size
                    tp_pips = abs(tp - current_price) / pip_size

                    journal.append({
                        "symbol": symbol, "side": signal, "entry": current_price,
                        "sl": sl, "tp": tp, "status": "OPEN", "time": datetime.now().isoformat()
                    })
                    
                    send_telegram(
                        f"✅ {signal} {symbol} EXECUTED\n"
                        f"Entry: {current_price:.5f}\n"
                        f"SL (ATR): {sl:.5f} ({sl_pips:.1f} pips)\n"
                        f"TP: {tp:.5f} ({tp_pips:.1f} pips)\n"
                        f"Ticket: {order.ticket}"
                    )
                except Exception as e:
                    error_msg = f"⚠️ [ORDER ERROR] Failed to place {signal} on {symbol}:\n{e}"
                    print(error_msg)
                    send_telegram(error_msg, markdown=False)
            else:
                # Debug Telegram
                if time.time() - last_forex_telegram_time > 3600:
                    trend_str = 'BULLISH' if is_bullish else 'BEARISH' if is_bearish else 'FLAT'
                    
                    debug_msg = (
                        f"🔍 *{symbol} ANALYSIS (HOLD)*\n\n"
                        f"Price: {current_price:.4f}\n"
                        f"4H Trend: {trend_str}\n"
                        f"20-Bar Low: {recent_low:.4f}\n"
                        f"20-Bar High: {recent_high:.4f}\n"
                        f"Wick Swept Low? {swept_low}\n"
                        f"Wick Swept High? {swept_high}\n"
                        f"Result: No setup. Waiting."
                    )
                    send_telegram(debug_msg)
                    print(debug_msg)
                    last_forex_telegram_time = time.time()
                else:
                    print(f"  [HOLD] {symbol}: No clean sweep + confirmation setup.")

        save_journal(journal)

    except Exception as e:
        # ✅ GLOBAL CATCH: If anything breaks outside the specific try blocks, alert on Telegram
        fatal_msg = f"🚨 [FATAL BOT CRASH]\n{str(e)}"
        print(fatal_msg)
        send_telegram(fatal_msg, markdown=False)

if __name__ == "__main__":
    run_strategy()
