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
    """Checks if Forex market is open based on UTC time."""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    if weekday == 4 and now.hour >= 21: return False # Friday night
    if weekday == 5: return False # Saturday
    if weekday == 6 and now.hour < 21: return False # Sunday before open
    return True

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e: print(f"Telegram Error: {e}")

def get_pip_size(symbol):
    """Returns the standard pip size for the given asset."""
    if "JPY" in symbol: return 0.01
    if "XAU" in symbol: return 0.1  # Gold standard pip
    return 0.0001 # Standard forex pair

def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, "r") as f: return json.load(f)
        except: return []
    return []

def save_journal(journal):
    with open(JOURNAL_FILE, "w") as f: json.dump(journal, f, indent=4)

def check_closed_trades(client, account_id, journal):
    """
    The Autopsy Agent: Checks MT5 history to calculate exact PnL and Pips 
    for any trade marked as OPEN in the journal that is no longer active.
    """
    updated = False
    try:
        # 1. Ask Exness: What is currently open?
        open_positions = client.positions.get(account_id)
        open_symbols = [p.symbol for p in open_positions]
        
        # 2. Fetch history from the last 5 days to find the closing deals
        from_date = datetime.now(timezone.utc) - timedelta(days=5)
        to_date = datetime.now(timezone.utc) + timedelta(days=1)
        
        # Note: Adjust the history method if your TickerAll SDK version uses a slightly different name
        history = client.history.deals_get(account_id, from_date=from_date, to_date=to_date)
        
        for trade in journal:
            if trade['status'] == "OPEN":
                # If our journal says it's open, but Exness says it's gone...
                if trade['symbol'] not in open_symbols:
                    exit_deal = None
                    
                    # Search history for the exact exit deal
                    for deal in history:
                        # MT5 entry type 1 = OUT (Closing a trade)
                        if deal.symbol == trade['symbol'] and deal.entry == 1: 
                            exit_deal = deal
                            break
                    
                    if exit_deal:
                        close_price = exit_deal.price
                        profit = exit_deal.profit
                        
                        # Calculate Pips
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
                        # Fallback if history hasn't updated yet
                        send_telegram(f"🔔 {trade['symbol']} trade closed. Check Exness app for exact PnL.")
                    
                    trade['status'] = "CLOSED"
                    updated = True
    except Exception as e:
        print(f"Error resolving history: {e}")
        
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
        save_journal(journal) # Save the new CLOSED statuses immediately

    # 2. SECOND: Hunt for new trades
    for symbol in SYMBOLS:
        # Skip if we already have an open trade for this specific symbol
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

        # 3. Logic: 4H Trend
        df_4h['ema20'] = df_4h['close'].ewm(span=20, adjust=False).mean()
        df_4h['ema50'] = df_4h['close'].ewm(span=50, adjust=False).mean()
        
        is_bullish = df_4h['ema20'].iloc[-1] > df_4h['ema50'].iloc[-1]
        is_bearish = df_4h['ema20'].iloc[-1] < df_4h['ema50'].iloc[-1]

        # 4. Logic: 15M Sweep
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

        # 5. Execution
        print(f"🚨 SIGNAL: {signal} {symbol} | Entry: {current_price} | SL: {sl} | TP: {tp}")
        
        try:
            order = client.orders.place(
                account_id, type="market", symbol=symbol, side="BUY" if signal == "LONG" else "SELL",
                volume=0.01, stop_loss=sl, take_profit=tp
            )
            
            # Calculate the Risk and Reward in Pips for the opening message
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
