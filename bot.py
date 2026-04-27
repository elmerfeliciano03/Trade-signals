"""
Telegram Signal Bot - DEBUG VERSION
Shows which conditions are passing/failing
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
SIGNAL_COOLDOWN = 3600

ASSETS = {
    "Gold (XAUUSD)": {"symbol": "GC=F", "alt_symbol": "GLD", "emoji": "🥇"},
    "Crude Oil WTI": {"symbol": "CL=F", "alt_symbol": "USO", "emoji": "🛢️"},
    "Bitcoin": {"symbol": "BTC-USD", "alt_symbol": None, "emoji": "₿"},
    "Ethereum": {"symbol": "ETH-USD", "alt_symbol": None, "emoji": "Ξ"}
}

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

def check_conditions(prices_h4, prices_h1, prices_m5):
    if len(prices_h4) < 20:
        return {"long": False, "long_details": {}, "short": False, "short_details": {}, "price": 0}
    
    close_h4 = prices_h4[-2] if len(prices_h4) >= 2 else prices_h4[-1]
    close_h1 = prices_h1[-2] if len(prices_h1) >= 2 else prices_h1[-1]
    close_m5 = prices_m5[-2] if len(prices_m5) >= 2 else prices_m5[-1]
    current_price = close_m5
    
    ema200_h4 = calculate_ema(prices_h4, min(200, len(prices_h4) - 1))
    ema200_h1 = calculate_ema(prices_h1, min(200, len(prices_h1) - 1))
    ema200_m5 = calculate_ema(prices_m5, min(200, len(prices_m5) - 1))
    ema9_m5 = calculate_ema(prices_m5, min(9, len(prices_m5) - 1))
    
    macd_line, signal_line = calculate_macd(prices_m5)
    
    long_conditions = {}
    short_conditions = {}
    
    # Conditions with actual values for debugging
    if ema200_h4:
        long_conditions[f"H4 Price (${close_h4:.2f}) > EMA200 (${ema200_h4:.2f})"] = close_h4 > ema200_h4
        short_conditions[f"H4 Price (${close_h4:.2f}) < EMA200 (${ema200_h4:.2f})"] = close_h4 < ema200_h4
    
    if ema200_h1:
        long_conditions[f"H1 Price (${close_h1:.2f}) > EMA200 (${ema200_h1:.2f})"] = close_h1 > ema200_h1
        short_conditions[f"H1 Price (${close_h1:.2f}) < EMA200 (${ema200_h1:.2f})"] = close_h1 < ema200_h1
    
    if ema200_m5:
        long_conditions[f"M5 Price (${close_m5:.2f}) > EMA200 (${ema200_m5:.2f})"] = close_m5 > ema200_m5
        short_conditions[f"M5 Price (${close_m5:.2f}) < EMA200 (${ema200_m5:.2f})"] = close_m5 < ema200_m5
    
    if ema9_m5 and ema200_m5:
        long_conditions[f"EMA9 (${ema9_m5:.2f}) > EMA200 (${ema200_m5:.2f})"] = ema9_m5 > ema200_m5
        short_conditions[f"EMA9 (${ema9_m5:.2f}) < EMA200 (${ema200_m5:.2f})"] = ema9_m5 < ema200_m5
        long_conditions[f"Price (${close_m5:.2f}) > EMA9 (${ema9_m5:.2f})"] = close_m5 > ema9_m5
        short_conditions[f"Price (${close_m5:.2f}) < EMA9 (${ema9_m5:.2f})"] = close_m5 < ema9_m5
    
    if macd_line and signal_line and len(macd_line) >= 3:
        macd_curr = macd_line[-2]
        signal_curr = signal_line[-2]
        long_conditions[f"MACD (${macd_curr:.2f}) < 0 and Signal (${signal_curr:.2f}) < 0"] = (macd_curr < 0 and signal_curr < 0)
        short_conditions[f"MACD (${macd_curr:.2f}) > 0 and Signal (${signal_curr:.2f}) > 0"] = (macd_curr > 0 and signal_curr > 0)
        
        macd_prev = macd_line[-3]
        signal_prev = signal_line[-3]
        long_cross = macd_prev < signal_prev and macd_curr >= signal_curr
        short_cross = macd_prev > signal_prev and macd_curr <= signal_curr
        long_conditions["MACD just crossed Signal from BELOW"] = long_cross
        short_conditions["MACD just crossed Signal from ABOVE"] = short_cross
    
    long_signal = all(long_conditions.values())
    short_signal = all(short_conditions.values())
    
    return {
        "long": long_signal,
        "long_details": long_conditions,
        "short": short_signal,
        "short_details": short_conditions,
        "price": current_price
    }

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

def main():
    log.info("=" * 60)
    log.info("📊 TRADING SIGNAL BOT - DEBUG MODE")
    log.info("Showing which conditions are passing/failing")
    log.info("=" * 60)
    
    for name, asset_config in ASSETS.items():
        log.info(f"\n🔍 {name} ({asset_config['symbol']})")
        log.info("-" * 40)
        
        prices_h4, prices_h1, prices_m5 = get_asset_data(name, asset_config)
        
        if prices_h4 is None or len(prices_h4) < 10:
            log.warning(f"⚠️ Insufficient data")
            continue
        
        result = check_conditions(prices_h4, prices_h1, prices_m5)
        current_price = result.get("price", 0)
        
        log.info(f"💰 Current Price: ${current_price:,.2f}")
        log.info("")
        
        log.info("📈 LONG CONDITIONS:")
        for condition, passed in result["long_details"].items():
            status = "✅" if passed else "❌"
            log.info(f"  {status} {condition}")
        
        if result["long"]:
            log.info(f"\n🟢 LONG SIGNAL ACTIVE!")
            msg = f"<b>🟢 LONG SIGNAL — {asset_config['emoji']} {name}</b>\n\nPrice: ${current_price:,.2f}\nAll conditions met!"
            send_telegram(msg)
        
        log.info("")
        log.info("📉 SHORT CONDITIONS:")
        for condition, passed in result["short_details"].items():
            status = "✅" if passed else "❌"
            log.info(f"  {status} {condition}")
        
        if result["short"]:
            log.info(f"\n🔴 SHORT SIGNAL ACTIVE!")
            msg = f"<b>🔴 SHORT SIGNAL — {asset_config['emoji']} {name}</b>\n\nPrice: ${current_price:,.2f}\nAll conditions met!"
            send_telegram(msg)
        
        log.info("=" * 40)
        time.sleep(2)

if __name__ == "__main__":
    main()
