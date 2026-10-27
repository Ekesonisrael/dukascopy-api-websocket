import os
import pandas as pd
import json
import time
from datetime import datetime, timezone, timedelta
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

SYMBOLS = [
    "XAUUSD", "XAUUSDm",     # Gold
    "EURUSD", "EURUSDm",     # Euro
    "GBPUSD", "GBPUSDm",     # Pound
    "USDJPY", "USDJPYm"      # Yen
]

JOURNAL_FILE = "forex_journal.json"

def is_market_open():
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    if weekday == 4 and now.hour >= 21: return False # Friday
    if weekday == 5: return False # Saturday
    if weekday == 6 and now.hour < 21: return False # Sunday
    return True

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e: print(f"Telegram Error: {e}")

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
        detail = client.accounts.get(account_id)
        open_positions = detail.positions if hasattr(detail, 'positions') else []
        open_symbols = [p.symbol for p in open_positions]
        
        for trade in journal:
            if trade['status'] == "OPEN":
                if trade['symbol'] not in open_symbols:
                    # Fetch history for this specific symbol
                    trades = client.history.get(account_id, symbol=trade['symbol'], limit=50)
                    
                    exit_deal = None
                    if trades:
                        for deal in trades:
                            # Aggressive matching for MT5 closing deals
                            entry_attr = getattr(deal, 'entry', None)
                            type_attr = getattr(deal, 'type', None)
                            
                            is_exit = entry_attr in [1, 'OUT', 'out'] or (type_attr in [1, 'SELL', 'sell'] and entry_attr != 0)
                            
                            if is_exit:
                                exit_deal = deal
                                break
                    
                    if exit_deal:
                        close_price = getattr(exit_deal, 'price', trade['entry'])
                        profit = getattr(exit_deal, 'profit', 0.0)
                        
                        pip_size = get_pip_size(trade['symbol'])
                        if trade['side'] == "LONG":
                            pips = (close_price - trade['entry']) / pip_size
                        else:
                            pips = (trade['entry'] - close_price) / pip_size
                            
                        result_emoji = "🟢 WIN" if profit > 0 else "🔴 LOSS"
                        
                        report = (
                            f"🔔 *TRADE CLOSED: {trade['symbol']}*\n\n"
                            f"Result: {result_emoji}\n"
                            f"Side: {trade['side']}\n"
                            f"Entry: {trade['entry']:.5f}\n"
                            f"Exit: {close_price:.5f}\n"
                            f"Pips: {pips:+.1f}\n"
                            f"Profit: ${profit:+.2f}\n\n"
                            f"🧠 Strategy: Liquidity Sweep"
                        )
                        send_telegram(report)
                        print(report)
                    else:
                        send_telegram(f"🔔 {trade['symbol']} trade closed. Check Exness app for PnL.")
                    
                    trade['status'] = "CLOSED"
                    updated = True
    except Exception as e:
        print(f"Error resolving history: {e}")
        send_telegram(f"⚠️ Resolver Error: Could not fetch history. Error: {e}")
        
    return updated

def run_strategy():
    if not is_market_open():
        print(f"--- Market is CLOSED (Weekend). Sleeping... ---")
        return 

    print(f"--- Market is OPEN. Starting Session at {datetime.now()} ---")
    
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

    # 1. FIRST: Run the Autopsy on old trades
    if check_closed_trades(client, account_id, journal):
        save_journal(journal)

    # 2. SECOND: Hunt for new trades
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
                df_4h = pd.DataFrame([{'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close} for c in candles_4h])
                df_15m = pd.DataFrame([{'open': c.open, 'high': c.high, 'low': c.low, 'close': c.close} for c in candles_15m])
                break 
            except Exception:
                if attempt == 0: time.sleep(1) 

        if df_4h.empty or df_15m.empty or len(df_4h) < 30:
            continue 

        df_4h['ema20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
        df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
        
        is_bullish = df_4h['ema20'].iloc[-1] > df_4h['ema50'].iloc[-1]
        is_bearish = df_4h['ema20'].iloc[-1] < df_4h['ema50'].iloc[-1]

        current_price = float(df_15m['close'].iloc[-1])
        recent_low = float(df_15m['low'].iloc[-20:-1].min())
        recent_high = float(df_15m['high'].iloc[-20:-1].max())

        swept_low = df_15m['low'].iloc[-1] < recent_low and current_price > recent_low
        swept_high = df_15m['high'].iloc[-1] > recent_high and current_price < recent_high

        signal = None
        if is_bullish and swept_low:
            signal, sl = "LONG", recent_low * 0.9995
            tp = current_price + (current_price - sl) * 2.5
        elif is_bearish and swept_high:
            signal, sl = "SHORT", recent_high * 1.0005
            tp = current_price - (sl - current_price) * 2.5
        else:
            print(f"  [HOLD] {symbol}: No sweep setup.")
            continue

        print(f"🚨 SIGNAL: {signal} {symbol} | Entry: {current_price} | SL: {sl} | TP: {tp}")
        
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
                f"✅ {signal} {symbol}\n"
                f"Entry: {current_price:.5f}\n"
                f"SL: {sl:.5f} ({sl_pips:.1f} pips)\n"
                f"TP: {tp:.5f} ({tp_pips:.1f} pips)\n"
                f"Ticket: {order.ticket}"
            )
        except Exception as e:
            print(f"Order Failed: {e}")

    save_journal(journal)

if __name__ == "__main__":
    run_strategy()
