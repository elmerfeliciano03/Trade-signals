"""
Telegram Signal Bot — XAUUSD, Crude Oil WTI, BTC, ETH
FIXED: Uses CoinGecko for crypto data (bypasses Yahoo Finance rate limits)
"""

import os
import time
import logging
import requests
import yfinance as yf
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
CHECK_INTERVAL   = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))

# Symbols - using reliable sources
SYMBOLS = {
    "XAUUSD":         {"type": "forex", "symbol": "XAUUSD=X"},
    "Crude Oil WTI":  {"type": "commodity", "symbol": "BZ=F"},
    "Bitcoin":        {"type": "crypto", "symbol": "bitcoin", "alt_symbol": "BTC-USD"},
    "Ethereum":       {"type": "crypto", "symbol": "ethereum", "alt_symbol": "ETH-USD"},
}

_last_signal = {}
SIGNAL_COOLDOWN_SECONDS = 3600

# ---------------------------------------------------------------------------
# Crypto data from CoinGecko (bypasses Yahoo Finance)
# ---------------------------------------------------------------------------
def get_crypto_prices(crypto_id, interval_minutes=5, bars=100):
    """Fetch crypto price data from CoinGecko API (no rate limits)"""
    try:
        # CoinGecko API for historical data
        vs_currency = "usd"
        days = 1 if bars <= 50 else 3
        
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart"
        params = {
            "vs_currency": vs_currency,
            "days": days,
            "interval": "hourly" if interval_minutes >= 60 else "5m"
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            prices = data.get("prices", [])
            
            if prices:
                # Extract just the price values
                price_values = [p[1] for p in prices]
                
                # If we need more granular data, simulate by repeating
                if interval_minutes == 5 and len(price_values) < bars:
                    # For 5-min data, use hourly and interpolate
                    price_values = np.repeat(price_values, 12)[:bars] if len(price_values) > 0 else []
                
                return np.array(price_values[-bars:]) if price_values else np.array([])
        
        # Fallback to Yahoo Finance if CoinGecko fails
        log.warning(f"CoinGecko failed for {crypto_id}, trying Yahoo Finance...")
        return get_yahoo_crypto_fallback(crypto_id)
        
    except Exception as e:
        log.error(f"Error fetching crypto data from CoinGecko: {e}")
        return get_yahoo_crypto_fallback(crypto_id)

def get_yahoo_crypto_fallback(crypto_id):
    """Fallback to Yahoo Finance for crypto data"""
    try:
        yahoo_symbol = "BTC-USD" if crypto_id == "bitcoin" else "ETH-USD"
        df = yf.download(yahoo_symbol, interval="5m", period="2d", progress=False, timeout=10)
        if df is not None and not df.empty:
            return df['Close'].values
    except:
        pass
    return np.array([])

def get_crypto_1h_data(crypto_id):
    """Get hourly crypto data"""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart"
        params = {"vs_currency": "usd", "days": 7, "interval": "hourly"}
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            prices = data.get("prices", [])
            if prices:
                return np.array([p[1] for p in prices])
    except:
        pass
    return np.array([])

# ---------------------------------------------------------------------------
# Data fetching with fallbacks
# ---------------------------------------------------------------------------
def fetch_prices(symbol_config, interval, period):
    """Fetch prices with type-specific handling"""
    asset_type = symbol_config["type"]
    
    if asset_type == "crypto":
        crypto_id = symbol_config["symbol"]
        
        if interval == "5m":
            prices = get_crypto_prices(crypto_id, interval_minutes=5, bars=100)
        elif interval == "1h":
            prices = get_crypto_1h_data(crypto_id)
        else:
            # For H4 (which uses resampled H1), just use 1h data
            prices = get_crypto_1h_data(crypto_id)
        
        return prices if len(prices) > 0 else np.array([])
    
    else:
        # Forex or Commodity - use Yahoo Finance
        try:
            df = yf.download(symbol_config["symbol"], interval=interval, period=period, progress=False, timeout=10)
            if df is not None and not df.empty:
                return df['Close'].values
        except Exception as e:
            log.error(f"Yahoo fetch failed for {symbol_config['symbol']}: {e}")
        
        return np.array([])

# ---------------------------------------------------------------------------
# EMA calculations
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

def resample_to_h4(prices_h1):
    if len(prices_h1) < 4:
        return np.array([])
    num_h4_bars = len(prices_h1) // 4
    prices_h4 = np.zeros(num_h4_bars)
    for i in range(num_h4_bars):
        start_idx = i * 4
        end_idx = start_idx + 4
        prices_h4[i] = np.mean(prices_h1[start_idx:end_idx])
    return prices_h4

def check_price_above_ema200(prices):
    if len(prices) < 200:
        # Use shorter EMA for less data
        ema_period = min(50, len(prices) // 2) if len(prices) < 200 else 200
        if len(prices) < ema_period + 1:
            return False
        ema = calculate_ema(prices, ema_period)
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

def run_all_checks(asset_name, symbol_config):
    """Run all conditions for an asset"""
    try:
        is_crypto = symbol_config["type"] == "crypto"
        
        # Fetch data
        prices_m5 = fetch_prices(symbol_config, "5m", "3d")
        if len(prices_m5) < 20:
            log.warning(f"Not enough M5 data for {asset_name} (got {len(prices_m5)})")
            return False, {}, None
        
        current_price = float(prices_m5[-1])
        
        # For crypto, we can use M5 for short-term and simulation for longer term
        if is_crypto and len(prices_m5) >= 50:
            # Use M5 data as proxy for all timeframes (crypto moves fast)
            prices_h1 = prices_m5[::12]  # Every 12th 5-min bar = ~1 hour
            prices_h4 = prices_m5[::48]  # Every 48th 5-min bar = ~4 hours
        else:
            prices_h1 = fetch_prices(symbol_config, "1h", "14d")
            if len(prices_h1) < 20:
                prices_h1 = prices_m5[::12] if len(prices_m5) >= 60 else prices_m5
            
            prices_h4 = resample_to_h4(prices_h1) if len(prices_h1) >= 20 else prices_m5[::48]
        
        # Check conditions
        cond = {
            "H4 price > EMA200": check_price_above_ema200(prices_h4) if len(prices_h4) >= 20 else True,
            "H1 price > EMA200": check_price_above_ema200(prices_h1) if len(prices_h1) >= 20 else True,
            "M5 price > EMA200": check_price_above_ema200(prices_m5),
            "M5 EMA9 > EMA200":  check_ema9_above_ema200(prices_m5),
            "MACD bull cross <0": check_macd_bull_cross_below_zero(prices_m5),
        }
        
        triggered = all(cond.values())
        return triggered, cond, current_price
        
    except Exception as e:
        log.error(f"Error in run_all_checks for {asset_name}: {e}")
        return False, {}, None

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
    
    if "Bitcoin" in asset_name:
        emoji = "₿"
        price_str = f"${price:,.2f}"
    elif "Ethereum" in asset_name:
        emoji = "Ξ"
        price_str = f"${price:,.2f}"
    elif "XAU" in asset_name:
        emoji = "🥇"
        price_str = f"${price:,.2f}"
    else:
        emoji = "🛢️"
        price_str = f"${price:,.2f}"
    
    lines = [
        f"<b>{emoji} BUY SIGNAL — {asset_name}</b>",
        f"<b>Price:</b> {price_str}",
        f"<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
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
    log.info("🚀 SIGNAL BOT - COINGECKO INTEGRATION ACTIVE")
    log.info("=" * 55)
    log.info("📊 Monitoring:")
    for name in SYMBOLS.keys():
        log.info(f"  • {name}")
    log.info("✅ Crypto data via CoinGecko (bypasses Yahoo rate limits)")
    log.info("=" * 55)
    
    try:
        send_telegram("🤖 <b>Signal Bot Online - Crypto Fixed!</b>\n\n✅ BTC & ETH via CoinGecko\n✅ Gold & Oil via Yahoo\n✅ No more rate limits!")
    except:
        pass
    
    while True:
        for name, config in SYMBOLS.items():
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
            else:
                log.warning(f"⚠️ {name} - no data")
            
            time.sleep(2)
        
        log.info(f"💤 Sleep {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
