"""
Telegram Signal Bot - SIGNALS ONLY
Only sends Telegram messages when ALL conditions are met
"""

import os
import logging
import requests
from datetime import datetime, timezone
import time
import numpy as np
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============ CONFIGURATION ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

COOLDOWN_FILE = "/tmp/last_signal.txt"
SIGNAL_COOLDOWN = 3600  # 1 hour between signals per asset

ASSETS = {
    "Gold": {"symbol": "GC=F", "alt_symbol": "GLD", "emoji": "🥇"},
    "Crude Oil": {"symbol": "CL=F", "alt_symbol": "USO", "emoji": "🛢️"},
    "Bitcoin": {"symbol": "BTC-USD", "alt_symbol": None, "emoji": "₿"},
    "Ethereum": {"symbol": "ETH-USD", "alt_symbol": None, "emoji": "Ξ"}
}

# ============ COOLDOWN ============
def check_cooldown(asset):
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            for line in f:
                if line.startswith(f"{asset}:"):
                    last = float(line.split(':')[1])
                    if (datetime.now(timezone.utc).timestamp() - last) < SIGNAL_COOLDOWN:
                        return True
    except:
        pass
    return False

def save_cooldown(asset):
    try:
        with open(COOLDOWN_FILE, 'a') as f:
            f.write(f"{asset}:{datetime.now(timezone.utc).timestamp()}\n")
    except:
        pass

# ============ DATA FETCHING ============
def fetch_data(symbol, interval, period="5d"):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        log.debug(f"Error fetching {symbol}: {e}")
    return None

def fetch_with_fallback(asset_name, asset_config, interval, period="5d"):
    symbol = asset_config["symbol"]
    df = fetch_data(symbol, interval, period)
    if df is not None and not df.empty:
        return df
    alt_symbol = asset_config.get("alt_symbol")
    if alt_symbol:
        df = fetch_data(alt_symbol, interval, period)
        if df is not None and not df.empty:
            return df
    return None

def get_asset_data(asset_name, asset_config):
    try:
        df_h1 = fetch_with_fallback(asset_name, asset_config, "1h", "60d")
        if df_h1 is None or df_h1.empty:
            return None, None, None
        
        prices_h1 = df_h1['Close'].values
        num_h4 = len(prices_h1) // 4
        prices_h4 = np.array([np.mean(prices_h1[i*4:(i+1)*4]) for i in range(num_h4)])
        
        df_m5 = fetch_with_fallback(asset_name, asset_config, "5m", "2d")
        if df_m5 is None or df_m5.empty:
            return None, None, None
        
        prices_m5 = df_m5['Close'].values
        return prices_h4, prices_h1, prices_m5
    except Exception as e:
        log.error(f"Error getting data for {asset_name}: {e}")
        return None, None, None

# ============ TECHNICAL INDICATORS ============
def calculate_ema(prices, period):
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

def get_ema_series(prices, period):
    if len(prices) < period:
        return []
    ema_values = []
    multiplier = 2 / (period + 1)
    ema = prices[0]
    for price in prices:
        ema = (price - ema) * multiplier + ema
        ema_values.append(ema)
    return ema_values

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow:
        return None, None
    ema_fast_values = get_ema_series(prices, fast)
    ema_slow_values = get_ema_series(prices, slow)
    if len(ema_fast_values) < slow or len(ema_slow_values) < slow:
        return None, None
    macd_line = [ema_fast_values[i] - ema_slow_values[i] for i in range(len(ema_slow_values))]
    signal_line = get_ema_series(macd_line, signal)
    if len(signal_line) < 2:
        return None, None
    return macd_line, signal_line

# ============ SIGNAL CONDITIONS ============
def check_conditions(prices_h4, prices_h1, prices_m5):
    if len(prices_h4) < 20:
        return {"long": False, "short": False, "price": 0}
    
    close_h4 = prices_h4[-2] if len(prices_h4) >= 2 else prices_h4[-1]
    close_h1 = prices_h1[-2] if len(prices_h1) >= 2 else prices_h1[-1]
    close_m5 = prices_m5[-2] if len(prices_m5) >= 2 else prices_m5[-1]
    current_price = close_m5
    
    ema200_h4 = calculate_ema(prices_h4, min(200, len(prices_h4) - 1))
    ema200_h1 = calculate_ema(prices_h1, min(200, len(prices_h1) - 1))
    ema200_m5 = calculate_ema(prices_m5, min(200, len(prices_m5) - 1))
    ema9_m5 = calculate_ema(prices_m5, min(9, len(prices_m5) - 1))
    
    macd_line, signal_line = calculate_macd(prices_m5)
    
    # LONG CONDITIONS
    long_conditions = []
    if ema200_h4 and close_h4 > ema200_h4:
        long_conditions.append(True)
    else:
        long_conditions.append(False)
    
    if ema200_h1 and close_h1 > ema200_h1:
        long_conditions.append(True)
    else:
        long_conditions.append(False)
    
    if ema200_m5 and close_m5 > ema200_m5:
        long_conditions.append(True)
    else:
        long_conditions.append(False)
    
    if ema9_m5 and ema200_m5 and ema9_m5 > ema200_m5:
        long_conditions.append(True)
    else:
        long_conditions.append(False)
    
    if ema9_m5 and close_m5 > ema9_m5:
        long_conditions.append(True)
    else:
        long_conditions.append(False)
    
    if macd_line and signal_line and len(macd_line) >= 3:
        macd_prev = macd_line[-3]
        macd_curr = macd_line[-2]
        signal_prev = signal_line[-3]
        signal_curr = signal_line[-2]
        long_cross = (macd_prev < signal_prev and macd_curr >= signal_curr)
        long_below_zero = (macd_curr < 0 and signal_curr < 0)
        long_conditions.append(long_cross and long_below_zero)
    else:
        long_conditions.append(False)
    
    # SHORT CONDITIONS
    short_conditions = []
    if ema200_h4 and close_h4 < ema200_h4:
        short_conditions.append(True)
    else:
        short_conditions.append(False)
    
    if ema200_h1 and close_h1 < ema200_h1:
        short_conditions.append(True)
    else:
        short_conditions.append(False)
    
    if ema200_m5 and close_m5 < ema200_m5:
        short_conditions.append(True)
    else:
        short_conditions.append(False)
    
    if ema9_m5 and ema200_m5 and ema9_m5 < ema200_m5:
        short_conditions.append(True)
    else:
        short_conditions.append(False)
    
    if ema9_m5 and close_m5 < ema9_m5:
        short_conditions.append(True)
    else:
        short_conditions.append(False)
    
    if macd_line and signal_line and len(macd_line) >= 3:
        macd_prev = macd_line[-3]
        macd_curr = macd_line[-2]
        signal_prev = signal_line[-3]
        signal_curr = signal_line[-2]
        short_cross = (macd_prev > signal_prev and macd_curr <= signal_curr)
        short_above_zero = (macd_curr > 0 and signal_curr > 0)
        short_conditions.append(short_cross and short_above_zero)
    else:
        short_conditions.append(False)
    
    return {
        "long": all(long_conditions),
        "short": all(short_conditions),
        "price": current_price
    }

# ============ TELEGRAM ============
def send_signal(asset_name, emoji, signal_type, price):
    message = f"""<b>{'🟢' if signal_type == 'LONG' else '🔴'} {signal_type} ENTRY SIGNAL — {emoji} {asset_name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>All 6 conditions met!</b>

<code>{'🚀 Consider LONG position' if signal_type == 'LONG' else '📉 Consider SHORT position'}
📍 {'Stop loss below swing low' if signal_type == 'LONG' else 'Stop loss above swing high'}
🎯 Take profit at 2:1 risk/reward</code>"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        r.raise_for_status()
        log.info(f"✅ {signal_type} signal sent for {asset_name}")
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

# ============ MAIN ============
def main():
    log.info("=" * 60)
    log.info("📊 SIGNAL BOT ACTIVE - Monitoring 4 assets")
    log.info("=" * 60)
    
    for name, asset_config in ASSETS.items():
        prices_h4, prices_h1, prices_m5 = get_asset_data(name, asset_config)
        
        if prices_h4 is None or len(prices_h4) < 10:
            log.warning(f"⚠️ {name} - insufficient data")
            continue
        
        result = check_conditions(prices_h4, prices_h1, prices_m5)
        price = result["price"]
        
        # Check LONG signal
        if result["long"] and price > 0:
            if not check_cooldown(f"{name}_LONG"):
                if send_signal(name, asset_config["emoji"], "LONG", price):
                    save_cooldown(f"{name}_LONG")
        
        # Check SHORT signal
        if result["short"] and price > 0:
            if not check_cooldown(f"{name}_SHORT"):
                if send_signal(name, asset_config["emoji"], "SHORT", price):
                    save_cooldown(f"{name}_SHORT")
        
        time.sleep(2)
    
    log.info(f"✅ Cycle complete - {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")

if __name__ == "__main__":
    main()
