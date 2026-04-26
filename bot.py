"""
Telegram Signal Bot - SIGNALS ONLY (With Retry Logic)
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

COOLDOWN_FILE = "/tmp/last_signal.txt"
SIGNAL_COOLDOWN = 3600  # 1 hour between signals per asset

ASSETS = {
    "XAUUSD (Gold)": {
        "type": "forex",
        "from": "XAU",
        "to": "USD",
        "emoji": "🥇"
    },
    "Crude Oil WTI": {
        "type": "commodity",
        "symbol": "WTI",
        "emoji": "🛢️"
    },
    "Bitcoin": {
        "type": "crypto",
        "id": "bitcoin",
        "emoji": "₿"
    },
    "Ethereum": {
        "type": "crypto",
        "id": "ethereum",
        "emoji": "Ξ"
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

# ============ ALPHA VANTAGE (Gold & Oil) ============
def get_alpha_vantage_price(symbol, asset_type):
    """Get current price with retry logic"""
    for attempt in range(3):  # Try 3 times
        try:
            if asset_type == "forex":
                params = {
                    "function": "CURRENCY_EXCHANGE_RATE",
                    "from_currency": "XAU",
                    "to_currency": "USD",
                    "apikey": ALPHA_VANTAGE_KEY
                }
            else:
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
                
                if asset_type == "forex":
                    rate = data.get("Realtime Currency Exchange Rate", {}).get("5. Exchange Rate", "")
                    if rate:
                        return float(rate)
                else:
                    time_series = data.get("Time Series (5min)", {})
                    if time_series:
                        latest = list(time_series.values())[0]
                        return float(latest.get("4. close", 0))
            
            # If we hit rate limit, wait before retry
            if "Note" in str(data) or "rate limit" in str(data).lower():
                log.warning(f"Rate limit hit for {symbol}, waiting...")
                time.sleep(2)
                
        except Exception as e:
            log.error(f"Alpha Vantage error (attempt {attempt+1}): {e}")
            time.sleep(1)
    
    return 0

def get_alpha_vantage_historical(symbol, asset_type):
    """Get historical data with retry"""
    for attempt in range(2):
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
                for date in sorted(time_series.keys())[-60:]:  # Last 60 days
                    prices.append(float(time_series[date]["4. close"]))
                if prices:
                    return prices
        except:
            pass
        time.sleep(1)
    return []

# ============ COINGECKO (Crypto) ============
def get_crypto_price(crypto_id):
    """Get crypto price with retry"""
    for attempt in range(3):
        try:
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": crypto_id, "vs_currencies": "usd"}
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                price = data.get(crypto_id, {}).get("usd", 0)
                if price > 0:
                    return price
        except Exception as e:
            log.error(f"CoinGecko error for {crypto_id} (attempt {attempt+1}): {e}")
        time.sleep(1)
    return 0

def get_crypto_historical(crypto_id, days=30):
    """Get crypto historical data"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart"
        params = {"vs_currency": "usd", "days": days, "interval": "daily"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            prices = r.json().get("prices", [])
            return [p[1] for p in prices]
    except Exception as e:
        log.error(f"Crypto historical error for {crypto_id}: {e}")
    return []

# ============ TECHNICAL INDICATORS ============
def calculate_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def generate_signal(name, asset_config, current_price, historical):
    """Generate BUY signal based on SMA crossover"""
    if not historical or len(historical) < 20 or current_price == 0:
        return None, None
    
    sma_20 = calculate_sma(historical, 20)
    sma_50 = calculate_sma(historical, 50) if len(historical) >= 50 else None
    
    if sma_20 is None:
        return None, None
    
    # Buy signal: Price above SMA20 (simpler condition)
    if current_price > sma_20:
        # Check if it just crossed above
        if historical[-2] <= sma_20:
            return "BUY", f"Price crossed above SMA20 (${sma_20:.2f})"
    
    # Stronger buy signal: Price above both SMAs
    if sma_50 and current_price > sma_20 and current_price > sma_50:
        if historical[-2] <= sma_20 or historical[-2] <= sma_50:
            return "BUY", f"Price above SMA20 (${sma_20:.2f}) and SMA50 (${sma_50:.2f})"
    
    return None, None

# ============ TELEGRAM ============
def send_signal(name, signal_type, reason, price, emoji):
    """Send ONLY trading signals"""
    message = f"""<b>🚀 {signal_type} — {emoji} {name}</b>

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

# ============ MAIN ============
def main():
    log.info("=" * 45)
    log.info("📊 SIGNAL BOT - ACTIVE (With Retry Logic)")
    log.info(f"Monitoring {len(ASSETS)} assets")
    
    # Check if Alpha Vantage key is set
    if not ALPHA_VANTAGE_KEY:
        log.warning("⚠️ ALPHA_VANTAGE_KEY not set! Gold/Oil will be unavailable.")
        log.info("Get free key at: https://www.alphavantage.co/support/#api-key")
    
    log.info("=" * 45)
    
    signals_found = 0
    
    for name, asset_config in ASSETS.items():
        log.info(f"🔍 Checking {name}...")
        
        # Get data based on type
        if asset_config["type"] in ["forex", "commodity"]:
            if not ALPHA_VANTAGE_KEY:
                log.warning(f"⚠️ {name} - Alpha Vantage key missing")
                continue
            
            if asset_config["type"] == "forex":
                current_price = get_alpha_vantage_price(asset_config["from"], "forex")
                historical = get_alpha_vantage_historical(asset_config["from"], "forex")
            else:
                current_price = get_alpha_vantage_price(asset_config["symbol"], "commodity")
                historical = get_alpha_vantage_historical(asset_config["symbol"], "commodity")
        else:  # crypto
            current_price = get_crypto_price(asset_config["id"])
            historical = get_crypto_historical(asset_config["id"], days=30)
        
        if current_price > 0:
            log.info(f"📊 {name} - ${current_price:,.2f}")
            
            # Generate signal
            signal_type, reason = generate_signal(name, asset_config, current_price, historical)
            
            if signal_type and reason:
                if not check_cooldown(name):
                    if send_signal(name, signal_type, reason, current_price, asset_config.get("emoji", "📊")):
                        save_cooldown(name)
                        signals_found += 1
                else:
                    log.info(f"⏰ {name} - signal blocked (cooldown)")
        else:
            log.warning(f"⚠️ {name} - price unavailable (retrying next cycle)")
        
        # Small delay between API calls to avoid rate limits
        time.sleep(2)
    
    if signals_found > 0:
        log.info(f"✅ Sent {signals_found} signal(s)")
    else:
        log.info("✅ No signals this cycle")
    log.info("=" * 45)

if __name__ == "__main__":
    main()
