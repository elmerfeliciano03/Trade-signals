"""
Telegram Signal Bot - 3-Condition Rules with Detailed Logging
FIXED: No duplicate signals
"""

import os
import logging
import requests
from datetime import datetime, timezone
import time
import numpy as np
import yfinance as yf
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============ CONFIGURATION ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Use persistent file for cooldown tracking (prevents duplicates across runs)
COOLDOWN_FILE = "/tmp/signal_tracker.json"
SIGNAL_COOLDOWN = 3600  # 1 hour between same asset+signal
MACD_CROSS_FILE = "/tmp/macd_cross_tracker.json"  # Track last cross per asset

# ASSETS with Yahoo Finance symbols
ASSETS = {
    "Gold": {"symbol": "GC=F", "alt_symbol": "GLD", "emoji": "🥇"},
    "Crude Oil": {"symbol": "CL=F", "alt_symbol": "USO", "emoji": "🛢️"},
    "Bitcoin": {"symbol": "BTC-USD", "alt_symbol": None, "emoji": "₿"},
    "Ethereum": {"symbol": "ETH-USD", "alt_symbol": None, "emoji": "Ξ"},
    "Nasdaq": {"symbol": "MNQ=F", "alt_symbol": "NQ=F", "emoji": "📊"},
    "S&P 500": {"symbol": "MES=F", "alt_symbol": "ES=F", "emoji": "📈"}
}

# ============ PERSISTENT TRACKING (Prevents duplicates across runs) ============
def load_tracker(file_path):
    """Load tracker JSON file"""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_tracker(file_path, data):
    """Save tracker JSON file"""
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        log.debug(f"Failed to save tracker: {e}")

def check_signal_allowed(asset, signal_type, cross_time):
    """Check if we should send this signal (prevents duplicates)"""
    tracker = load_tracker(COOLDOWN_FILE)
    key = f"{asset}_{signal_type}"
    
    now = datetime.now(timezone.utc).timestamp()
    
    # Check cooldown
    if key in tracker:
        last_time = tracker[key]
        if (now - last_time) < SIGNAL_COOLDOWN:
            return False, f"cooldown ({int((now - last_time)/60)} min remaining)"
    
    return True, "allowed"

def save_signal_time(asset, signal_type):
    """Save when signal was sent"""
    tracker = load_tracker(COOLDOWN_FILE)
    key = f"{asset}_{signal_type}"
    tracker[key] = datetime.now(timezone.utc).timestamp()
    save_tracker(COOLDOWN_FILE, tracker)

def check_macd_cross_already_sent(asset, cross_type, cross_time_str):
    """Prevent sending same MACD cross multiple times"""
    tracker = load_tracker(MACD_CROSS_FILE)
    key = f"{asset}_{cross_type}_cross"
    
    if key in tracker and tracker[key] == cross_time_str:
        return True  # Already sent this exact cross
    return False

def save_macd_cross(asset, cross_type, cross_time_str):
    """Record that this MACD cross was sent"""
    tracker = load_tracker(MACD_CROSS_FILE)
    key = f"{asset}_{cross_type}_cross"
    tracker[key] = cross_time_str
    save_tracker(MACD_CROSS_FILE, tracker)

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
            log.debug(f"{asset_name}: Using fallback {alt_symbol}")
            return df
    return None

def get_asset_data(asset_name, asset_config):
    try:
        df_h1 = fetch_with_fallback(asset_name, asset_config, "1h", "60d")
        if df_h1 is None or df_h1.empty:
            return None, None, None
        
        prices_h1 = df_h1['Close'].values
        
        # Resample to H4 (every 4 hours)
        num_h4 = len(prices_h1) // 4
        if num_h4 < 5:
            log.warning(f"⚠️ {asset_name}: Only {num_h4} H4 bars available")
        
        prices_h4 = np.array([np.mean(prices_h1[i*4:(i+1)*4]) for i in range(num_h4)])
        
        # Fetch M5 data - get more for better MACD detection
        df_m5 = fetch_with_fallback(asset_name, asset_config, "5m", "2d")
        if df_m5 is None or df_m5.empty:
            log.warning(f"⚠️ {asset_name}: Cannot fetch M5 data for MACD")
            return prices_h4, prices_h1, None
        
        prices_m5 = df_m5['Close'].values
        # Also get timestamps for MACD cross tracking
        timestamps_m5 = df_m5.index.tolist()
        
        return prices_h4, prices_h1, prices_m5, timestamps_m5
    except Exception as e:
        log.error(f"Error getting data for {asset_name}: {e}")
        return None, None, None, None

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
    if len(prices) < slow + signal:
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

# ============ SIGNAL CONDITIONS WITH DEDUPLICATION ============
def check_conditions(prices_h4, prices_h1, prices_m5, timestamps_m5, asset_name):
    if len(prices_h4) < 5 or len(prices_h1) < 20:
        return {"long": False, "short": False, "price": 0, 
                "long_details": {}, "short_details": {},
                "cross_time_str": None}
    
    close_h4 = prices_h4[-2] if len(prices_h4) >= 2 else prices_h4[-1]
    close_h1 = prices_h1[-2] if len(prices_h1) >= 2 else prices_h1[-1]
    current_price = close_h1
    
    ema200_h4 = calculate_ema(prices_h4, min(200, len(prices_h4) - 1))
    ema200_h1 = calculate_ema(prices_h1, min(200, len(prices_h1) - 1))
    
    cross_occurred = False
    cross_type = None
    cross_time_str = None
    macd_value = "Insufficient M5 data"
    
    if prices_m5 is not None and len(prices_m5) >= 40 and timestamps_m5:
        macd_line, signal_line = calculate_macd(prices_m5)
        
        if macd_line and signal_line and len(macd_line) >= 5:
            # Check last 5 bars for crossover to ensure we don't miss it
            for i in range(-3, 0):
                macd_prev = macd_line[i-1]
                macd_curr = macd_line[i]
                signal_prev = signal_line[i-1]
                signal_curr = signal_line[i]
                
                if i >= -len(timestamps_m5):
                    bar_time = timestamps_m5[i]
                    bar_time_str = bar_time.strftime('%Y-%m-%d %H:%M')
                    
                    # LONG cross: MACD crosses ABOVE Signal (bullish)
                    if macd_prev < signal_prev and macd_curr >= signal_curr:
                        # Check if below zero for LONG signal
                        if macd_curr <= 0 and signal_curr <= 0:
                            cross_occurred = True
                            cross_type = "LONG"
                            cross_time_str = bar_time_str
                            macd_value = f"MACD crossed ABOVE Signal at {bar_time_str} (below zero)"
                            break
                    
                    # SHORT cross: MACD crosses BELOW Signal (bearish)
                    elif macd_prev > signal_prev and macd_curr <= signal_curr:
                        # Check if above zero for SHORT signal
                        if macd_curr >= 0 and signal_curr >= 0:
                            cross_occurred = True
                            cross_type = "SHORT"
                            cross_time_str = bar_time_str
                            macd_value = f"MACD crossed BELOW Signal at {bar_time_str} (above zero)"
                            break
            
            if not cross_occurred:
                # Get latest values for display
                macd_curr = macd_line[-2]
                signal_curr = signal_line[-2]
                macd_value = f"MACD: ${macd_curr:.2f}, Signal: ${signal_curr:.2f} | No recent crossover"
    
    # LONG conditions
    long_details = {}
    long_details["H4 Price > EMA200"] = {
        "passed": ema200_h4 and close_h4 > ema200_h4,
        "value": f"${close_h4:.2f} > ${ema200_h4:.2f}" if ema200_h4 else "Insufficient data"
    }
    long_details["H1 Price > EMA200"] = {
        "passed": ema200_h1 and close_h1 > ema200_h1,
        "value": f"${close_h1:.2f} > ${ema200_h1:.2f}" if ema200_h1 else "Insufficient data"
    }
    long_details["MACD cross (bullish, below zero)"] = {
        "passed": cross_occurred and cross_type == "LONG",
        "value": macd_value
    }
    
    # SHORT conditions
    short_details = {}
    short_details["H4 Price < EMA200"] = {
        "passed": ema200_h4 and close_h4 < ema200_h4,
        "value": f"${close_h4:.2f} < ${ema200_h4:.2f}" if ema200_h4 else "Insufficient data"
    }
    short_details["H1 Price < EMA200"] = {
        "passed": ema200_h1 and close_h1 < ema200_h1,
        "value": f"${close_h1:.2f} < ${ema200_h1:.2f}" if ema200_h1 else "Insufficient data"
    }
    short_details["MACD cross (bearish, above zero)"] = {
        "passed": cross_occurred and cross_type == "SHORT",
        "value": macd_value
    }
    
    long_signal = all(d["passed"] for d in long_details.values())
    short_signal = all(d["passed"] for d in short_details.values())
    
    return {
        "long": long_signal,
        "short": short_signal,
        "price": current_price,
        "long_details": long_details,
        "short_details": short_details,
        "cross_occurred": cross_occurred,
        "cross_type": cross_type,
        "cross_time_str": cross_time_str
    }

# ============ LOGGING FUNCTIONS ============
def log_long_conditions(asset_name, details, current_price):
    log.info(f"📈 {asset_name} - LONG CONDITIONS (Price: ${current_price:,.2f}):")
    passed_count = 0
    for condition, data in details.items():
        status = "✅" if data["passed"] else "❌"
        if data["passed"]:
            passed_count += 1
        log.info(f"  {status} {condition}: {data['value']}")
    log.info(f"  → {passed_count}/3 conditions met")
    return passed_count == 3

def log_short_conditions(asset_name, details, current_price):
    log.info(f"📉 {asset_name} - SHORT CONDITIONS (Price: ${current_price:,.2f}):")
    passed_count = 0
    for condition, data in details.items():
        status = "✅" if data["passed"] else "❌"
        if data["passed"]:
            passed_count += 1
        log.info(f"  {status} {condition}: {data['value']}")
    log.info(f"  → {passed_count}/3 conditions met")
    return passed_count == 3

# ============ TELEGRAM ============
def send_signal(asset_name, emoji, signal_type, price, cross_time_str):
    """Send signal to Telegram - simplified to avoid duplicates"""
    direction = "🟢 LONG" if signal_type == "LONG" else "🔴 SHORT"
    
    message = f"""<b>{direction} SIGNAL — {emoji} {asset_name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
<b>Signal Type:</b> 3-Condition MACD Strategy

<code>{'✅ All 3 conditions met for LONG entry' if signal_type == 'LONG' else '✅ All 3 conditions met for SHORT entry'}</code>"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        r.raise_for_status()
        log.info(f"✅ {signal_type} signal sent to Telegram for {asset_name}")
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

# ============ MAIN ============
def main():
    log.info("=" * 70)
    log.info("📊 TRADING SIGNAL BOT - MACD CROSSOVER DETECTION")
    log.info("=" * 70)
    log.info("LONG:  H4 > EMA200 + H1 > EMA200 + MACD bullish cross (below zero)")
    log.info("SHORT: H4 < EMA200 + H1 < EMA200 + MACD bearish cross (above zero)")
    log.info("=" * 70)
    log.info(f"Monitoring {len(ASSETS)} assets:")
    for name, config in ASSETS.items():
        log.info(f"  • {name} ({config['symbol']})")
    log.info("=" * 70)
    
    signals_sent = 0
    
    for name, asset_config in ASSETS.items():
        log.info(f"\n{'='*50}")
        log.info(f"🔍 Analyzing {name} ({asset_config['symbol']})...")
        log.info(f"{'='*50}")
        
        result = get_asset_data(name, asset_config)
        if result is None:
            continue
        
        prices_h4, prices_h1, prices_m5, timestamps_m5 = result
        
        if prices_h4 is None or len(prices_h4) < 5:
            log.warning(f"⚠️ {name} - Insufficient H4 data")
            continue
        
        if prices_h1 is None or len(prices_h1) < 20:
            log.warning(f"⚠️ {name} - Insufficient H1 data")
            continue
        
        conditions = check_conditions(prices_h4, prices_h1, prices_m5, timestamps_m5, name)
        current_price = conditions.get("price", 0)
        
        # Display conditions
        long_all_met = log_long_conditions(name, conditions["long_details"], current_price)
        short_all_met = log_short_conditions(name, conditions["short_details"], current_price)
        
        # Send LONG signal (with deduplication)
        if long_all_met and conditions.get("cross_time_str"):
            # Check if this exact MACD cross was already sent
            if not check_macd_cross_already_sent(name, "LONG", conditions["cross_time_str"]):
                allowed, reason = check_signal_allowed(name, "LONG", conditions["cross_time_str"])
                if allowed:
                    log.info(f"\n🟢🔔 SENDING LONG SIGNAL for {name}!")
                    if send_signal(name, asset_config["emoji"], "LONG", current_price, conditions["cross_time_str"]):
                        save_signal_time(name, "LONG")
                        save_macd_cross(name, "LONG", conditions["cross_time_str"])
                        signals_sent += 1
                else:
                    log.info(f"⏰ {name} LONG - {reason}")
            else:
                log.info(f"🔄 {name} LONG - MACD cross already sent (duplicate prevented)")
        
        # Send SHORT signal (with deduplication)
        if short_all_met and conditions.get("cross_time_str"):
            if not check_macd_cross_already_sent(name, "SHORT", conditions["cross_time_str"]):
                allowed, reason = check_signal_allowed(name, "SHORT", conditions["cross_time_str"])
                if allowed:
                    log.info(f"\n🔴🔔 SENDING SHORT SIGNAL for {name}!")
                    if send_signal(name, asset_config["emoji"], "SHORT", current_price, conditions["cross_time_str"]):
                        save_signal_time(name, "SHORT")
                        save_macd_cross(name, "SHORT", conditions["cross_time_str"])
                        signals_sent += 1
                else:
                    log.info(f"⏰ {name} SHORT - {reason}")
            else:
                log.info(f"🔄 {name} SHORT - MACD cross already sent (duplicate prevented)")
        
        if not long_all_met and not short_all_met:
            log.info(f"\n📊 {name} - No signal (conditions not met)")
        
        time.sleep(2)
    
    log.info(f"\n{'='*70}")
    log.info(f"✅ Cycle complete. Sent {signals_sent} signal(s).")
    log.info(f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"{'='*70}\n")

if __name__ == "__main__":
    main()
