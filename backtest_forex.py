import os
import pandas as pd
import numpy as np
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

SYMBOL = "XAUUSD"
RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 1.5
FEE_RATE = 0.0001

TRADING_HOURS_START = 13
TRADING_HOURS_END = 17

# ✅ 200 days = 4,800 hours
LOOKBACK_HOURS = 4800 

def run_backtest():
    print(f"Connecting to MT5 to fetch {LOOKBACK_HOURS} hours (approx 200 days) of data for {SYMBOL}...")
    
    client = Tickerall(api_key=TICKERALL_API_KEY, timeout=60)
    try:
        session = client.sessions.start(
            broker="mt5", server=MT5_SERVER, account=MT5_ACCOUNT,
            password=MT5_PASSWORD, terminal_type="MOBILE"
        )
        account_id = session.account_id
    except Exception as e:
        print(f"Failed to connect to MT5: {e}")
        return

    try:
        # ✅ Use hours instead of count
        candles_15m = client.candles.get(
            account_id, 
            symbol=SYMBOL, 
            hours=LOOKBACK_HOURS, 
            timeframe="M15"
        )
    except Exception as e:
        print(f"Failed to fetch candles: {e}")
        return

    if not candles_15m:
        print("No data returned from MT5. Trying a smaller window (90 days)...")
        try:
            # Fallback to 90 days (2160 hours)
            candles_15m = client.candles.get(
                account_id, 
                symbol=SYMBOL, 
                hours=2160, 
                timeframe="M15"
            )
        except Exception as e:
            print(f"Fallback fetch also failed: {e}")
            return

    if not candles_15m:
        print("Still no data returned. The broker might not have enough M15 history.")
        return

    print(f"Successfully fetched {len(candles_15m)} candles. Processing data...")

    # ... The rest of your data processing and backtest loop remains exactly the same ...
    # (You can paste your existing DataFrame construction and loop logic here)
