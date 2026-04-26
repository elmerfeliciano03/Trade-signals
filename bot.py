"""
Telegram Signal Bot - SIGNALS ONLY (No Spam)
Only sends messages when a real trading signal is detected
"""

import os
import logging
import requests
from datetime import datetime, timezone
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============ CONFIGURATION ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

# Cooldown tracking (prevents repeated signals for same asset)
COOLDOWN_FILE = "/tmp/last_signal.txt"
SIGNAL_COOLDOWN = 3600  # 1 hour between signals per asset

# Assets to monitor
ASSETS = {
    "XAUUSD (Gold)": {
        "type": "forex",
        "from": "XAU",
        "to": "USD",
        "emoji": "🥇",
        "api": "alpha_vantage"
    },
    "Crude Oil WTI": {
        "type": "commodity",
        "symbol": "WTI",
        "emoji": "🛢️",
        "api": "alpha_vantage"
    },
    "Bitcoin": {
        "type": "crypto",
        "id": "bitcoin",
        "emoji": "₿",
        "api": "coingecko"
    },
    "Ethereum": {
        "type": "crypto",
        "id": "ethereum",
        "emoji": "Ξ",
        "api": "coingecko"
    }
}

# ============ COOLDOWN FUNCTIONS ============
def check_cooldown(asset):
    """Check if signal was sent recently for this asset"""
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
    """Record that a signal was sent"""
    try:
        with open(COOLDOWN_FILE, 'a') as f:
            f.write(f"{asset}:{datetime.now(timezone.utc).timestamp()}\n")
    except:
        pass

# ============ ALPHA VANTAGE API (Gold & Oil) ============
def get_alpha_vantage_price(symbol, asset_type):
    """Get current price from Alpha Vantage"""
    try:
        if asset_type == "forex":
            params = {
                "function": "CURRENCY_EXCHANGE_RATE",
                "from_currency": "XAU",
                "to_currency": "USD",
                "apikey": ALPHA_VANTAGE_KEY
            }
            url = "https://www.alphavantage.co/query"
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                rate = data.get("Realtime Currency Exchange Rate", {}).get("5. Exchange Rate", "")
                if rate:
                    return float(rate)
        elif asset_type == "commodity":
            params = {
                "function": "CRUDE_OIL_INTRADAY",
                "symbol": symbol,
                "interval": "5min",
                "apikey": ALPHA_VANTAGE_KEY
            }
            url = "https://www.alphavantage.co/query"
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                time_series = data.get("Time Series (5min)", {})
                if time_series:
                    latest = list(time_series.values())[0]
                    return float(latest.get("4. close", 0))
    except Exception as e:
        log.error(f"Alpha Vantage error: {e}")
    return 0

def get_alpha_vantage_historical(symbol, asset_type):
    """Get historical data for indicators"""
    try:
        if asset_type == "forex":
            params = {
                "function": "FX_DAILY",
                "from_symbol": "XAU",
                "to_symbol": "USD",
                "outputsize": "compact",
                "apikey": ALPHA_VANTAGE_KEY
            }
        else:
            params = {
                "function": "CRUDE_OIL_INTRADAY",
                "symbol": symbol,
                "interval": "daily",
                "outputsize": "compact",
                "apikey": ALPHA_VANTAGE_KEY
            }
        
        url = "https://www.alphavantage.co/query"
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            time_series = data.get("Time Series FX (Daily)", {}) or data.get("Time Series (Daily)", {})
            prices = []
            for date in sorted(time_series.keys()):
                prices.append(float(time_series[date]["4. close"]))
            return prices
    except Exception as e:
        log.error(f"Alpha Vantage historical error: {e}")
    return []

# ============ COINGECKO API (Crypto) ============
def get_crypto_price(crypto_id):
    """Get current price from CoinGecko"""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": crypto_id, "vs_currencies": "usd"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get(crypto_id, {}).get("usd", 0)
    except Exception as e:
        log.error(f"Crypto price error: {e}")
    return 0

def get_crypto_historical(crypto_id, days=30):
    """Get historical prices for indicators"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart"
        params = {"vs_currency": "usd", "days": days, "interval": "daily"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            return [p[1] for p in prices]
    except Exception as e:
        log.error(f"Crypto historical error: {e}")
    return []

# ============ TECHNICAL INDICATORS ============
def calculate_sma(prices, period):
    """Simple moving average"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def generate_signal(name, asset_config, current_price, historical):
    """Generate BUY signal based on technical indicators"""
    if not historical or len(historical) < 50 or current_price == 0:
        return None, None
    
    sma_20 = calculate_sma(historical, 20)
    sma_50 = calculate_sma(historical, 50)
    
    if sma_20 is None or sma_50 is None:
        return None, None
    
    # BUY Signal: Price above both moving averages (uptrend confirmation)
    if current_price > sma_20 and current_price > sma_50:
        if historical[-2] <= sma_20 or historical[-2] <= sma_50:
            return "BUY", f"Price crossed above SMA20 (${sma_20:.2f}) and SMA50 (${sma_50:.2f})"
    
    return None, None

# ============ TELEGRAM - SIGNALS ONLY ============
def send_signal(name, signal_type, reason, price, emoji):
    """Send ONLY trading signals - NO status messages"""
    action = "🚀 BUY" if signal_type == "BUY" else "⚠️ SELL"
    
    message = f"""<b>{action} — {emoji} {name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>Signal:</b> {reason}"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        r.raise_for_status()
        log.info(f"✅ Signal sent for {name}")
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

# ============ ASSET-SPECIFIC DATA ============
def get_asset_data(asset_name, asset_config):
    """Get current price and historical data"""
    asset_type = asset_config["type"]
    
    if asset_type == "crypto":
        crypto_id = asset_config["id"]
        current = get_crypto_price(crypto_id)
        historical = get_crypto_historical(crypto_id, days=60)
        return current, historical
    elif asset_type == "forex":
        current = get_alpha_vantage_price(asset_config["from"], "forex")
        historical = get_alpha_vantage_historical(asset_config["from"], "forex")
        return current, historical
    elif asset_type == "commodity":
        current = get_alpha_vantage_price(asset_config["symbol"], "commodity")
        historical = get_alpha_vantage_historical(asset_config["symbol"], "commodity")
        return current, historical
    
    return 0, []

# ============ MAIN FUNCTION - SIGNALS ONLY ============
def main():
    log.info("=" * 45)
    log.info("📊 SIGNAL BOT - ACTIVE (Signals Only)")
    log.info(f"Monitoring {len(ASSETS)} assets")
    log.info("=" * 45)
    
    signals_found = 0
    
    for name, asset_config in ASSETS.items():
        # Get data
        current_price, historical = get_asset_data(name, asset_config)
        
        if current_price > 0 and historical:
            # Generate signal
            signal_type, reason = generate_signal(name, asset_config, current_price, historical)
            
            if signal_type and reason:
                # Check cooldown to avoid spam
                if not check_cooldown(name):
                    if send_signal(name, signal_type, reason, current_price, asset_config.get("emoji", "📊")):
                        save_cooldown(name)
                        signals_found += 1
                else:
                    log.info(f"⏰ {name} - signal blocked (cooldown)")
            else:
                log.info(f"📊 {name} - ${current_price:,.2f} (no signal)")
        else:
            log.warning(f"⚠️ {name} - data unavailable")
    
    log.info(f"✅ Cycle complete. {signals_found} signal(s) sent.")
    log.info("=" * 45)

if __name__ == "__main__":
    main()
