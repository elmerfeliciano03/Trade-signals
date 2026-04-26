"""
Telegram Signal Bot - Full Trading Signals
Monitors: XAUUSD (Gold), Crude Oil WTI, Bitcoin, Ethereum
"""

import os
import logging
import requests
from datetime import datetime, timezone
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Config
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Cooldown tracking (prevents spam)
COOLDOWN_FILE = "/tmp/last_signal.txt"
SIGNAL_COOLDOWN = 3600  # 1 hour between signals per asset

# Assets to monitor with CoinGecko IDs (crypto only for now)
# Note: Gold and Oil will be added once you get Alpha Vantage API key
ASSETS = {
    "Bitcoin": {
        "id": "bitcoin",
        "emoji": "₿",
        "type": "crypto"
    },
    "Ethereum": {
        "id": "ethereum", 
        "emoji": "Ξ",
        "type": "crypto"
    }
}

# ============ COOLDOWN FUNCTIONS ============
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

# ============ PRICE DATA FROM COINGECKO ============
def get_crypto_price(crypto_id):
    """Get current price from CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": crypto_id, "vs_currencies": "usd"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get(crypto_id, {}).get("usd", 0)
    except Exception as e:
        log.error(f"Price fetch error for {crypto_id}: {e}")
    return 0

def get_crypto_historical(crypto_id, days=7):
    """Get historical prices for indicator calculation"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart"
        params = {"vs_currency": "usd", "days": days, "interval": "daily"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            return [p[1] for p in prices]
    except Exception as e:
        log.error(f"Historical data error for {crypto_id}: {e}")
    return []

# ============ SIMPLE INDICATORS ============
def calculate_sma(prices, period):
    """Simple moving average"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def generate_signal(name, asset_data):
    """Generate buy/sell signal based on simple logic"""
    crypto_id = asset_data["id"]
    
    # Get current price and historical
    current_price = get_crypto_price(crypto_id)
    historical = get_crypto_historical(crypto_id, days=30)
    
    if current_price == 0 or len(historical) < 20:
        return None, None
    
    # Calculate moving averages
    sma_20 = calculate_sma(historical, 20)
    sma_50 = calculate_sma(historical, 50)
    
    if sma_20 is None or sma_50 is None:
        return None, None
    
    # Simple trend following signal
    # Buy signal: Price above both moving averages (uptrend)
    if current_price > sma_20 and current_price > sma_50:
        # Check if it's a new signal (price just crossed above)
        if historical[-2] <= sma_20 or historical[-2] <= sma_50:
            return "BUY", f"Price crossed above moving averages"
    
    # Sell signal: Price below both moving averages (downtrend)  
    elif current_price < sma_20 and current_price < sma_50:
        if historical[-2] >= sma_20 or historical[-2] >= sma_50:
            return "SELL", f"Price crossed below moving averages"
    
    return None, None

# ============ TELEGRAM MESSAGING ============
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        r.raise_for_status()
        log.info("✅ Telegram sent")
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

def build_signal_message(name, signal_type, reason, price):
    emoji = "🟢" if signal_type == "BUY" else "🔴"
    action = "🚀 BUY" if signal_type == "BUY" else "⚠️ SELL"
    
    return f"""<b>{emoji} {action} SIGNAL — {name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
<b>Reason:</b> {reason}

<code>Trade at your own risk. Always use stop losses!</code>"""

# ============ MAIN FUNCTION (Runs every 5 minutes) ============
def main():
    log.info("=" * 45)
    log.info("📊 TRADING SIGNAL BOT - ACTIVE")
    log.info(f"Monitoring: {', '.join(ASSETS.keys())}")
    log.info("=" * 45)
    
    signals_found = 0
    
    for name, asset_data in ASSETS.items():
        log.info(f"🔍 Checking {name}...")
        
        # Get signal
        signal_type, reason = generate_signal(name, asset_data)
        current_price = get_crypto_price(asset_data["id"])
        
        if signal_type and current_price > 0:
            # Check cooldown to avoid spam
            if not check_cooldown(name):
                message = build_signal_message(name, signal_type, reason, current_price)
                if send_telegram(message):
                    save_cooldown(name)
                    signals_found += 1
                    log.info(f"🔔 {signal_type} SIGNAL for {name} at ${current_price:,.2f}")
            else:
                log.info(f"⏰ {name} - signal but cooldown active")
        elif current_price > 0:
            log.info(f"✅ {name} - no signal at ${current_price:,.2f}")
        else:
            log.warning(f"⚠️ {name} - price unavailable")
    
    # Status update (every 12th run = hourly)
    if signals_found > 0:
        log.info(f"📢 Sent {signals_found} signal(s) this cycle")
    
    log.info("💤 Bot cycle complete. Waiting {:.0f} minutes...".format(300/60))
    log.info("=" * 45)

if __name__ == "__main__":
    main()
