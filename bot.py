"""
Telegram Signal Bot - Using Alpha Vantage & CoinGecko (No Yahoo Finance)
"""

import os
import time
import logging
import requests
from datetime import datetime, timezone
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Config
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ALPHA_VANTAGE_KEY = os.environ["ALPHA_VANTAGE_KEY"]
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))

ASSETS = {
    "XAUUSD": {"type": "forex", "from": "XAU", "to": "USD"},
    "Crude Oil": {"type": "commodity", "symbol": "WTI"},
    "Bitcoin": {"type": "crypto", "id": "bitcoin"},
    "Ethereum": {"type": "crypto", "id": "ethereum"},
}

_last_signal = {}
SIGNAL_COOLDOWN = 3600

# ============ ALPHA VANTAGE (Forex & Commodities) ============
def get_alpha_vantage(symbol, interval="5min"):
    url = "https://www.alphavantage.co/query"
    params = {"function": "FX_INTRADAY" if "XAU" in symbol else "CRUDE_OIL_INTRADAY", 
              "from_symbol" if "XAU" in symbol else "symbol": symbol, 
              "to_symbol" if "XAU" in symbol else None: "USD" if "XAU" in symbol else None,
              "interval": interval, "apikey": ALPHA_VANTAGE_KEY, "outputsize": "compact"}
    params = {k: v for k, v in params.items() if v is not None}
    
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        series = data.get("Time Series FX (5min)") or data.get("Time Series (5min)") or {}
        prices = [float(v["4. close"]) for v in sorted(series.values())]
        return np.array(prices[-100:]) if prices else np.array([])
    except:
        return np.array([])

# ============ COINGECKO (Crypto) ============
def get_crypto(crypto_id):
    try:
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart", 
                        params={"vs_currency": "usd", "days": 3, "interval": "hourly"}, timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            return np.array([p[1] for p in prices[-100:]]) if prices else np.array([])
    except:
        pass
    return np.array([])

def get_crypto_5min(crypto_id):
    try:
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart",
                        params={"vs_currency": "usd", "days": 1, "interval": "5m"}, timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            return np.array([p[1] for p in prices[-100:]]) if prices else np.array([])
    except:
        pass
    return np.array([])

# ============ Technical Indicators ============
def ema(prices, period):
    if len(prices) < period:
        return np.array([])
    multiplier = 2 / (period + 1)
    ema_vals = np.zeros(len(prices))
    ema_vals[period-1] = np.mean(prices[:period])
    for i in range(period, len(prices)):
        ema_vals[i] = (prices[i] - ema_vals[i-1]) * multiplier + ema_vals[i-1]
    return ema_vals

def macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow:
        return np.array([]), np.array([])
    macd_line = ema(prices, fast) - ema(prices, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def check_price_above_ema200(prices):
    if len(prices) < 30:
        return False
    ema200 = ema(prices, min(200, len(prices)-1))
    if len(ema200) == 0:
        return False
    return prices[-2] > ema200[-2]

def check_ema9_above_ema200(prices):
    if len(prices) < 30:
        return False
    e9 = ema(prices, 9)
    e200 = ema(prices, min(200, len(prices)-1))
    return e9[-2] > e200[-2]

def check_macd_cross(prices):
    if len(prices) < 35:
        return False
    ml, sl = macd(prices)
    if len(ml) < 3:
        return False
    cross = ml[-3] < sl[-3] and ml[-2] >= sl[-2]
    below_zero = ml[-2] < 0 and sl[-2] < 0
    return cross and below_zero

# ============ Main Check ============
def check_asset(name, config):
    try:
        if config["type"] == "forex":
            prices_m5 = get_alpha_vantage(f"{config['from']}{config['to']}", "5min")
            prices_h1 = get_alpha_vantage(f"{config['from']}{config['to']}", "60min")
        elif config["type"] == "commodity":
            prices_m5 = get_alpha_vantage(config["symbol"], "5min")
            prices_h1 = get_alpha_vantage(config["symbol"], "60min")
        else:  # crypto
            prices_m5 = get_crypto_5min(config["id"])
            prices_h1 = get_crypto(config["id"])
        
        if len(prices_m5) < 10:
            return False, {}, None
        
        current = prices_m5[-1]
        prices_h4 = prices_h1[::4] if len(prices_h1) >= 20 else prices_m5[::48]
        
        cond = {
            "H4 price > EMA200": check_price_above_ema200(prices_h4) if len(prices_h4) >= 10 else True,
            "H1 price > EMA200": check_price_above_ema200(prices_h1) if len(prices_h1) >= 20 else True,
            "M5 price > EMA200": check_price_above_ema200(prices_m5),
            "M5 EMA9 > EMA200": check_ema9_above_ema200(prices_m5),
            "MACD bullish cross": check_macd_cross(prices_m5),
        }
        
        return all(cond.values()), cond, current
    except Exception as e:
        log.error(f"Error on {name}: {e}")
        return False, {}, None

# ============ Telegram ============
def send_tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        log.info("✅ Telegram sent")
    except:
        pass

def build_msg(name, cond, price):
    emoji = "🥇" if "XAU" in name else "🛢️" if "Crude" in name else "₿" if "Bitcoin" in name else "Ξ"
    lines = [f"<b>{emoji} BUY SIGNAL — {name}</b>", f"<b>Price:</b> ${price:,.2f}",
             f"<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC", "", "<b>Conditions:</b>"]
    lines.extend([f"  {'✅' if v else '❌'} {k}" for k, v in cond.items()])
    return "\n".join(lines)

# ============ Main Loop ============
def main():
    log.info("=" * 50)
    log.info("🚀 SIGNAL BOT - ALPHA VANTAGE + COINGECKO")
    log.info("NO YAHOO FINANCE - WORKS 24/7")
    log.info(f"Monitoring: {', '.join(ASSETS.keys())}")
    log.info("=" * 50)
    
    send_tg("🤖 <b>Signal Bot Online - NEW VERSION</b>\n\n✅ Using Alpha Vantage + CoinGecko\n✅ No Yahoo Finance!\n✅ Works 24/7")
    
    while True:
        for name, config in ASSETS.items():
            log.info(f"📊 Checking {name}...")
            triggered, cond, price = check_asset(name, config)
            
            if triggered and price:
                now = datetime.now(timezone.utc)
                last = _last_signal.get(name)
                if not last or (now - last).total_seconds() >= SIGNAL_COOLDOWN:
                    send_tg(build_msg(name, cond, price))
                    _last_signal[name] = now
                    log.info(f"🔔 SIGNAL for {name}!")
            elif price:
                log.info(f"❌ {name} - no signal")
            
            time.sleep(2)
        
        log.info(f"💤 Sleeping {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
