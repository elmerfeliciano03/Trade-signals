"""
Telegram Signal Bot — Uses Alpha Vantage (Forex/Commodities) + CoinGecko (Crypto)
NO Yahoo Finance dependency - works 24/7
"""

import os
import time
import logging
import requests
from datetime import datetime, timezone
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ALPHA_VANTAGE_KEY = os.environ["ALPHA_VANTAGE_KEY"]  # Get free from alphavantage.co
CHECK_INTERVAL   = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))

# Assets to monitor
ASSETS = {
    "XAUUSD": {"type": "forex", "from": "XAU", "to": "USD"},
    "Crude Oil WTI": {"type": "commodity", "symbol": "WTI"},
    "Bitcoin": {"type": "crypto", "id": "bitcoin"},
    "Ethereum": {"type": "crypto", "id": "ethereum"},
}

_last_signal = {}
SIGNAL_COOLDOWN_SECONDS = 3600

# ---------------------------------------------------------------------------
# Alpha Vantage API (Forex & Commodities)
# ---------------------------------------------------------------------------
def get_alpha_vantage_data(function, symbol, interval="5min"):
    """Fetch data from Alpha Vantage API"""
    url = "https://www.alphavantage.co/query"
    params = {
        "function": function,
        "symbol": symbol,
        "interval": interval,
        "apikey": ALPHA_VANTAGE_KEY,
        "outputsize": "compact"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        # Extract time series
        if "Time Series FX (5min)" in data:
            series = data["Time Series FX (5min)"]
        elif "Time Series (5min)" in data:
            series = data["Time Series (5min)"]
        else:
            return []
        
        # Extract closing prices
        prices = []
        for timestamp in sorted(series.keys()):
            if "close" in series[timestamp]:
                prices.append(float(series[timestamp]["4. close"]))
            elif "4. close" in series[timestamp]:
                prices.append(float(series[timestamp]["4. close"]))
        
        return prices
        
    except Exception as e:
        log.error(f"Alpha Vantage error: {e}")
        return []

def get_forex_prices(from_currency, to_currency, bars=100):
    """Get forex prices from Alpha Vantage"""
    prices = get_alpha_vantage_data("FX_INTRADAY", f"{from_currency}{to_currency}", "5min")
    return np.array(prices[-bars:]) if prices else np.array([])

def get_commodity_prices(commodity, bars=100):
    """Get commodity prices from Alpha Vantage"""
    symbol_map = {"WTI": "WTI", "BRENT": "BZ"}
    symbol = symbol_map.get(commodity, commodity)
    prices = get_alpha_vantage_data("CRUDE_OIL_INTRADAY", symbol, "5min")
    return np.array(prices[-bars:]) if prices else np.array([])

def get_alpha_vantage_hourly(function, symbol):
    """Get hourly data from Alpha Vantage"""
    url = "https://www.alphavantage.co/query"
    params = {
        "function": function,
        "symbol": symbol,
        "interval": "60min",
        "apikey": ALPHA_VANTAGE_KEY,
        "outputsize": "compact"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if "Time Series FX (60min)" in data:
            series = data["Time Series FX (60min)"]
        elif "Time Series (60min)" in data:
            series = data["Time Series (60min)"]
        else:
            return []
        
        prices = []
        for timestamp in sorted(series.keys()):
            if "close" in series[timestamp]:
                prices.append(float(series[timestamp]["4. close"]))
        
        return prices
    except:
        return []

# ---------------------------------------------------------------------------
# CoinGecko API (Crypto - works 24/7)
# ---------------------------------------------------------------------------
def get_crypto_prices(crypto_id, days=1):
    """Fetch crypto prices from CoinGecko"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart"
        params = {"vs_currency": "usd", "days": days, "interval": "hourly"}
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            prices = data.get("prices", [])
            if prices:
                # Get last 100 prices
                price_values = [p[1] for p in prices]
                return np.array(price_values[-100:])
    except Exception as e:
        log.error(f"CoinGecko error for {crypto_id}: {e}")
    
    return np.array([])

def get_crypto_hourly(crypto_id):
    """Get hourly crypto data"""
    return get_crypto_prices(crypto_id, days=7)

# ---------------------------------------------------------------------------
# Technical Indicators
# ---------------------------------------------------------------------------
def calculate_ema(prices, period):
    if len(prices) < period:
        return np.array([])
    ema = np.zeros(len(prices))
    multiplier = 2 / (period + 1)
    ema[period-1] = np.mean(prices[:period])
    for i in range(period, len(prices)):
        ema[i] = (prices[i] - ema[i-1]) * multiplier + ema[i-1]
    return ema

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow:
        return np.array([]), np.array([])
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if len(ema_fast) == 0 or len(ema_slow) == 0:
        return np.array([]), np.array([])
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    return macd_line, signal_line

def check_price_above_ema200(prices):
    if len(prices) < 200:
        # Use shorter EMA if not enough data
        period = min(50, len(prices) // 2)
        if len(prices) < period + 1:
            return False
        ema = calculate_ema(prices, period)
        if len(ema) == 0:
            return False
        return prices[-2] > ema[-2]
    
    ema200 = calculate_ema(prices, 200)
    if len(ema200) == 0:
        return False
    return prices[-2] > ema200[-2]

def check_ema9_above_ema200(prices):
    if len(prices) < 50:
        return False
    ema9 = calculate_ema(prices, 9)
    ema200 = calculate_ema(prices, min(200, len(prices) - 1))
    if len(ema9) == 0 or len(ema200) == 0:
        return False
    return ema9[-2] > ema200[-2]

def check_macd_bull_cross_below_zero(prices):
    if len(prices) < 35:
        return False
    macd_line, signal_line = calculate_macd(prices)
    if len(macd_line) < 3 or len(signal_line) < 3:
        return False
    
    mc_prev = macd_line[-3]
    sc_prev = signal_line[-3]
    mc_curr = macd_line[-2]
    sc_curr = signal_line[-2]
    
    cross_up = mc_prev < sc_prev and mc_curr >= sc_curr
    below_zero = mc_curr < 0 and sc_curr < 0
    
    return cross_up and below_zero

def resample_to_h4(prices_h1):
    """Resample hourly to 4-hour"""
    if len(prices_h1) < 4:
        return np.array([])
    num_h4_bars = len(prices_h1) // 4
    prices_h4 = np.zeros(num_h4_bars)
    for i in range(num_h4_bars):
        start_idx = i * 4
        end_idx = start_idx + 4
        prices_h4[i] = np.mean(prices_h1[start_idx:end_idx])
    return prices_h4

# ---------------------------------------------------------------------------
# Asset-specific data fetching
# ---------------------------------------------------------------------------
def fetch_asset_data(asset_name, asset_config):
    """Fetch price data based on asset type"""
    asset_type = asset_config["type"]
    
    try:
        if asset_type == "forex":
            # 5-min data
            prices_m5 = get_forex_prices(asset_config["from"], asset_config["to"])
            # Hourly data (approximate from 5-min)
            prices_h1 = prices_m5[::12] if len(prices_m5) >= 60 else prices_m5
            
        elif asset_type == "commodity":
            prices_m5 = get_commodity_prices(asset_config["symbol"])
            prices_h1 = prices_m5[::12] if len(prices_m5) >= 60 else prices_m5
            
        elif asset_type == "crypto":
            prices_m5 = get_crypto_prices(asset_config["id"], days=1)
            prices_h1 = get_crypto_hourly(asset_config["id"])
            if len(prices_h1) == 0:
                prices_h1 = prices_m5[::12] if len(prices_m5) >= 60 else prices_m5
        
        else:
            return None, None, None
        
        if len(prices_m5) < 10:
            return None, None, None
        
        current_price = prices_m5[-1]
        prices_h4 = resample_to_h4(prices_h1) if len(prices_h1) >= 20 else prices_m5[::48]
        
        return prices_m5, prices_h1, prices_h4, current_price
        
    except Exception as e:
        log.error(f"Error fetching {asset_name}: {e}")
        return None, None, None, None

def run_all_checks(asset_name, asset_config):
    """Run all trading conditions"""
    result = fetch_asset_data(asset_name, asset_config)
    if result[0] is None:
        return False, {}, None
    
    prices_m5, prices_h1, prices_h4, current_price = result
    
    cond = {
        "H4 price > EMA200": check_price_above_ema200(prices_h4) if len(prices_h4) >= 10 else True,
        "H1 price > EMA200": check_price_above_ema200(prices_h1) if len(prices_h1) >= 20 else True,
        "M5 price > EMA200": check_price_above_ema200(prices_m5),
        "M5 EMA9 > EMA200": check_ema9_above_ema200(prices_m5),
        "MACD bull cross <0": check_macd_bull_cross_below_zero(prices_m5),
    }
    
    triggered = all(cond.values())
    return triggered, cond, current_price

# ---------------------------------------------------------------------------
# Telegram Functions
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10).raise_for_status()
        log.info("✅ Telegram sent")
    except Exception as e:
        log.error(f"Telegram failed: {e}")

def build_message(asset_name: str, cond: dict, price: float) -> str:
    tick = lambda v: "✅" if v else "❌"
    
    emoji_map = {
        "XAUUSD": "🥇",
        "Crude Oil WTI": "🛢️",
        "Bitcoin": "₿",
        "Ethereum": "Ξ"
    }
    emoji = emoji_map.get(asset_name, "📊")
    
    lines = [
        f"<b>{emoji} BUY SIGNAL — {asset_name}</b>",
        f"<b>Price:</b> ${price:,.2f}",
        f"<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "",
        "<b>Conditions:</b>",
    ]
    lines.extend([f"  {tick(v)} {k}" for k, v in cond.items()])
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 55)
    log.info("🚀 SIGNAL BOT - ALPHA VANTAGE + COINGECKO")
    log.info("=" * 55)
    log.info("No Yahoo Finance dependency - works 24/7!")
    log.info(f"Monitoring: {', '.join(ASSETS.keys())}")
    
    # Send startup message
    try:
        msg = f"""🤖 <b>Signal Bot Online - NEW VERSION</b>

✅ Using Alpha Vantage (Forex/Commodities)
✅ Using CoinGecko (Crypto)
✅ No Yahoo Finance - works 24/7!

📊 Monitoring:
• 🥇 XAUUSD (Gold)
• 🛢️ Crude Oil WTI
• ₿ Bitcoin
• Ξ Ethereum

⏱️ Check interval: {CHECK_INTERVAL}s"""
        send_telegram(msg)
    except:
        pass
    
    while True:
        for name, config in ASSETS.items():
            log.info(f"📊 Checking {name}...")
            triggered, cond, price = run_all_checks(name, config)
            
            if triggered and price:
                now = datetime.now(timezone.utc)
                last = _last_signal.get(name)
                if last is None or (now - last).total_seconds() >= SIGNAL_COOLDOWN_SECONDS:
                    send_telegram(build_message(name, cond, price))
                    _last_signal[name] = now
                    log.info(f"🔔 SIGNAL for {name} at ${price:,.2f}")
            elif price:
                failed = [k for k, v in cond.items() if not v]
                if failed:
                    log.info(f"❌ {name} - no signal ({failed[0]})")
            
            time.sleep(2)
        
        log.info(f"💤 Sleeping {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
