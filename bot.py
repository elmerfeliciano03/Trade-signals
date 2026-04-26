"""
Telegram Signal Bot - Full Version
Monitors: XAUUSD (Gold), Crude Oil WTI, Bitcoin, Ethereum
Uses: Alpha Vantage (Gold/Oil) + CoinGecko (BTC/ETH)
"""

import os
import logging
import requests
from datetime import datetime, timezone
import time
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============ CONFIGURATION ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

# Cooldown tracking (prevents spam)
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

# ============ ALPHA VANTAGE API (Gold & Oil) ============
def get_alpha_vantage_price(symbol, asset_type):
    """Get current price from Alpha Vantage"""
    try:
        if asset_type == "forex":
            function = "CURRENCY_EXCHANGE_RATE"
            params = {
                "function": function,
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
            function = "CRUDE_OIL_INTRADAY"
            params = {
                "function": function,
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
        log.error(f"Alpha Vantage error for {symbol}: {e}")
    return 0

def get_alpha_vantage_historical(symbol, asset_type):
    """Get historical data for indicators"""
    try:
        if asset_type == "forex":
            function = "FX_DAILY"
            params = {
                "function": function,
                "from_symbol": "XAU",
                "to_symbol": "USD",
                "outputsize": "compact",
                "apikey": ALPHA_VANTAGE_KEY
            }
        elif asset_type == "commodity":
            function = "CRUDE_OIL_INTRADAY"
            params = {
                "function": function,
                "symbol": symbol,
                "interval": "daily",
                "outputsize": "compact",
                "apikey": ALPHA_VANTAGE_KEY
            }
        else:
            return []
        
        url = "https://www.alphavantage.co/query"
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if asset_type == "forex":
                time_series = data.get("Time Series FX (Daily)", {})
            else:
                time_series = data.get("Time Series (Daily)", {})
            
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
        log.error(f"Crypto price error for {crypto_id}: {e}")
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

def calculate_ema(prices, period):
    """Exponential moving average"""
    if len(prices) < period:
        return None
    
    multiplier = 2 / (period + 1)
    ema = prices[-period]
    
    for price in prices[-period+1:]:
        ema = (price - ema) * multiplier + ema
    
    return ema

def generate_signal(name, asset_data, current_price, historical):
    """Generate buy/sell signal based on multiple indicators"""
    if not historical or len(historical) < 50 or current_price == 0:
        return None, None
    
    # Calculate indicators
    sma_20 = calculate_sma(historical, 20)
    sma_50 = calculate_sma(historical, 50)
    ema_20 = calculate_ema(historical, 20)
    
    if sma_20 is None or sma_50 is None:
        return None, None
    
    signals = []
    
    # Signal 1: Golden Cross (50 SMA above 200 SMA)
    if len(historical) >= 200:
        sma_200 = calculate_sma(historical, 200)
        if sma_200 and sma_50 > sma_200 and historical[-2] <= sma_200:
            signals.append("Golden Cross formed (50 > 200 SMA)")
    
    # Signal 2: Price above key moving averages
    if current_price > sma_20 and current_price > sma_50:
        if historical[-2] <= sma_20 or historical[-2] <= sma_50:
            signals.append(f"Price broke above SMA20 (${sma_20:.2f}) and SMA50 (${sma_50:.2f})")
    
    # Signal 3: EMA crossover
    if ema_20:
        ema_50 = calculate_ema(historical, 50)
        if ema_50 and ema_20 > ema_50 and historical[-2] <= ema_50:
            signals.append("Bullish EMA crossover (20 EMA crossed above 50 EMA)")
    
    if signals:
        return "BUY", " | ".join(signals[:2])  # Max 2 reasons
    
    # Check for potential sell signals
    if current_price < sma_20 and current_price < sma_50:
        if historical[-2] >= sma_20 or historical[-2] >= sma_50:
            return "SELL", f"Price broke below SMA20 (${sma_20:.2f}) and SMA50 (${sma_50:.2f})"
    
    return None, None

# ============ ASSET-SPECIFIC DATA FETCHING ============
def get_asset_data(asset_name, asset_config):
    """Get current price and historical data for an asset"""
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

def build_signal_message(name, signal_type, reason, price, emoji):
    action = "🚀 BUY" if signal_type == "BUY" else "⚠️ SELL"
    color = "🟢" if signal_type == "BUY" else "🔴"
    
    return f"""<b>{color} {action} SIGNAL — {emoji} {name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>Reason:</b>
{reason}

<code>⚠️ Always use stop losses and proper risk management.</code>"""

def build_status_message():
    """Send a status update every hour"""
    return f"""<b>📊 Bot Status Update</b>

✅ Monitoring {len(ASSETS)} assets:
• 🥇 XAUUSD (Gold) - Alpha Vantage
• 🛢️ Crude Oil WTI - Alpha Vantage  
• ₿ Bitcoin - CoinGecko
• Ξ Ethereum - CoinGecko

🕐 Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
⏱️ Checking every 5 minutes

<code>Bot is running smoothly!</code>"""

# ============ MAIN FUNCTION ============
def main():
    log.info("=" * 50)
    log.info("📊 FULL TRADING SIGNAL BOT - ACTIVE")
    log.info(f"Monitoring {len(ASSETS)} assets:")
    for name in ASSETS.keys():
        log.info(f"  • {name}")
    log.info("=" * 50)
    
    # Send hourly status (check if it's time)
    current_hour = datetime.now(timezone.utc).hour
    status_file = "/tmp/last_status.txt"
    
    try:
        with open(status_file, 'r') as f:
            last_hour = int(f.read())
    except:
        last_hour = -1
    
    if current_hour != last_hour:
        send_telegram(build_status_message())
        with open(status_file, 'w') as f:
            f.write(str(current_hour))
    
    signals_found = 0
    
    for name, asset_config in ASSETS.items():
        log.info(f"🔍 Checking {name}...")
        
        # Get data
        current_price, historical = get_asset_data(name, asset_config)
        
        if current_price > 0 and historical:
            # Generate signal
            signal_type, reason = generate_signal(name, asset_config, current_price, historical)
            
            if signal_type and reason:
                if not check_cooldown(name):
                    message = build_signal_message(
                        name, signal_type, reason, current_price, 
                        asset_config.get("emoji", "📊")
                    )
                    if send_telegram(message):
                        save_cooldown(name)
                        signals_found += 1
                        log.info(f"🔔 {signal_type} SIGNAL for {name} at ${current_price:,.2f}")
                else:
                    log.info(f"⏰ {name} - signal but cooldown active")
            else:
                log.info(f"✅ {name} - no signal at ${current_price:,.2f}")
        else:
            log.warning(f"⚠️ {name} - data unavailable (check API key)")
    
    if signals_found > 0:
        log.info(f"📢 Sent {signals_found} signal(s) this cycle")
    
    log.info("💤 Cycle complete. Next check in 5 minutes.")
    log.info("=" * 50)

if __name__ == "__main__":
    main()
