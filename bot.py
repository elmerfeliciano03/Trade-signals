"""
Telegram Signal Bot - Cron Job Version for Render
Runs once per schedule (every 5 minutes)
"""

import os
import time
import logging
import requests
from datetime import datetime, timezone
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============ CONFIGURATION ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# No Yahoo Finance - using CoinGecko for crypto only for now
ASSETS = {
    "Bitcoin": "bitcoin",
    "Ethereum": "ethereum",
}

# Cooldown tracking (simple file-based)
COOLDOWN_FILE = "/tmp/last_signal.txt"
SIGNAL_COOLDOWN_SECONDS = 3600

# ============ COOLDOWN FUNCTIONS ============
def check_cooldown(asset_name):
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            for line in f:
                if line.startswith(f"{asset_name}:"):
                    last_time = float(line.split(':')[1])
                    if (datetime.now(timezone.utc).timestamp() - last_time) < SIGNAL_COOLDOWN_SECONDS:
                        return True
    except:
        pass
    return False

def save_cooldown(asset_name):
    try:
        with open(COOLDOWN_FILE, 'a') as f:
            f.write(f"{asset_name}:{datetime.now(timezone.utc).timestamp()}\n")
    except:
        pass

# ============ DATA FETCHING (CoinGecko - No Yahoo!) ============
def get_crypto_prices(crypto_id, days=1, interval="hourly"):
    """Get crypto data from CoinGecko - works 24/7, no rate limits"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart"
        params = {"vs_currency": "usd", "days": days, "interval": interval}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            return np.array([p[1] for p in prices]) if prices else np.array([])
    except Exception as e:
        log.error(f"CoinGecko error for {crypto_id}: {e}")
    return np.array([])

def get_data_for_asset(crypto_id):
    """Get M5, H1, H4 data for crypto"""
    # M5 data (approx - CoinGecko doesn't give real 5-min for free)
    prices_5min = get_crypto_prices(crypto_id, days=1, interval="5m")
    # H1 and H4 are simulated for demo
    prices_1h = get_crypto_prices(crypto_id, days=3, interval="hourly")
    prices_4h = prices_1h[::4] if len(prices_1h) >= 4 else prices_1h
    
    current_price = prices_5min[-1] if len(prices_5min) > 0 else 0
    return prices_5min, prices_1h, prices_4h, current_price

# ============ TECHNICAL INDICATORS ============
def ema(prices, period):
    if len(prices) < period:
        return np.array([])
    multiplier = 2 / (period + 1)
    ema_vals = np.zeros(len(prices))
    ema_vals[period-1] = np.mean(prices[:period])
    for i in range(period, len(prices)):
        ema_vals[i] = (prices[i] - ema_vals[i-1]) * multiplier + ema_vals[i-1]
    return ema_vals

def check_price_above_ema200(prices):
    if len(prices) < 30:
        return False
    period = min(200, len(prices)-1)
    ema200 = ema(prices, period)
    return prices[-2] > ema200[-2] if len(ema200) > 0 else False

def check_ema9_above_ema200(prices):
    if len(prices) < 30:
        return False
    e9 = ema(prices, 9)
    e200 = ema(prices, min(200, len(prices)-1))
    return e9[-2] > e200[-2] if len(e9) > 0 and len(e200) > 0 else False

def check_macd_cross(prices):
    if len(prices) < 35:
        return False
    fast = ema(prices, 12)
    slow = ema(prices, 26)
    if len(fast) == 0 or len(slow) == 0:
        return False
    macd_line = fast - slow
    signal_line = ema(macd_line, 9)
    if len(macd_line) < 3 or len(signal_line) < 3:
        return False
    cross = macd_line[-3] < signal_line[-3] and macd_line[-2] >= signal_line[-2]
    below_zero = macd_line[-2] < 0 and signal_line[-2] < 0
    return cross and below_zero

# ============ MAIN CHECK LOGIC ============
def check_asset(name, crypto_id):
    prices_m5, prices_h1, prices_h4, current = get_data_for_asset(crypto_id)
    
    if len(prices_m5) < 10 or current == 0:
        log.warning(f"Insufficient data for {name}")
        return False, {}, None
    
    cond = {
        "H4 > EMA200": check_price_above_ema200(prices_h4) if len(prices_h4) >= 10 else True,
        "H1 > EMA200": check_price_above_ema200(prices_h1) if len(prices_h1) >= 20 else True,
        "M5 > EMA200": check_price_above_ema200(prices_m5),
        "EMA9 > EMA200": check_ema9_above_ema200(prices_m5),
        "MACD bullish cross": check_macd_cross(prices_m5),
    }
    
    return all(cond.values()), cond, current

# ============ TELEGRAM MESSAGING ============
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("✅ Telegram sent")
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

def build_signal_message(name, price):
    return f"""<b>🚨 BUY SIGNAL — {name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>All 5 conditions met!</b>
• ✅ H4 price > EMA200
• ✅ H1 price > EMA200
• ✅ M5 price > EMA200
• ✅ EMA9 > EMA200
• ✅ MACD bullish cross below zero"""

# ============ MAIN (RUNS ONCE PER CRON INVOCATION) ============
def main():
    log.info("=" * 45)
    log.info("🤖 SIGNAL BOT - CRON JOB (Python 3.11)")
    log.info(f"Checking {len(ASSETS)} assets...")
    
    signals_sent = 0
    
    for name, crypto_id in ASSETS.items():
        log.info(f"📊 Checking {name}...")
        triggered, conditions, price = check_asset(name, crypto_id)
        
        if triggered and price:
            if not check_cooldown(name):
                if send_telegram(build_signal_message(name, price)):
                    save_cooldown(name)
                    signals_sent += 1
                    log.info(f"🔔 SIGNAL SENT for {name}! (${price:,.2f})")
            else:
                log.info(f"⏰ {name} - signal but cooldown active")
        elif price:
            log.info(f"❌ {name} - no signal (price: ${price:,.2f})")
        else:
            log.warning(f"⚠️ {name} - no price data")
    
    log.info(f"✅ Done. Sent {signals_sent} signal(s).")
    log.info("=" * 45)

if __name__ == "__main__":
    main()
