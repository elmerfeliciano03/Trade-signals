"""
Telegram Signal Bot - 3-Condition Rules with Detailed Logging
Shows exact values for each condition in logs

LONG:  H4 > EMA200 + H1 > EMA200 + MACD cross below 0 (within last 5 minutes)
SHORT: H4 < EMA200 + H1 < EMA200 + MACD cross above 0 (within last 5 minutes)

Assets: Gold, Oil, BTC, ETH, Nasdaq (MNQ=F), S&P500 (MES=F)
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
SIGNAL_COOLDOWN = 3600  # 1 hour between signals

# ASSETS with Yahoo Finance symbols
ASSETS = {
    "Gold": {"symbol": "GC=F", "alt_symbol": "GLD", "emoji": "🥇"},
    "Crude Oil": {"symbol": "CL=F", "alt_symbol": "USO", "emoji": "🛢️"},
    "Bitcoin": {"symbol": "BTC-USD", "alt_symbol": None, "emoji": "₿"},
    "Ethereum": {"symbol": "ETH-USD", "alt_symbol": None, "emoji": "Ξ"},
    "Nasdaq": {"symbol": "MNQ=F", "alt_symbol": "NQ=F", "emoji": "📊"},
    "S&P 500": {"symbol": "MES=F", "alt_symbol": "ES=F", "emoji": "📈"}
}

# ============ COOLDOWN FUNCTIONS ============
def check_cooldown(asset, signal_type):
    key = f"{asset}_{signal_type}"
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            for line in f:
                if line.startswith(f"{key}:"):
                    last = float(line.split(':')[1])
                    if (datetime.now(timezone.utc).timestamp() - last) < SIGNAL_COOLDOWN:
                        return True
    except:
        pass
    return False

def save_cooldown(asset, signal_type):
    key = f"{asset}_{signal_type}"
    try:
        with open(COOLDOWN_FILE, 'a') as f:
            f.write(f"{key}:{datetime.now(timezone.utc).timestamp()}\n")
    except:
        pass

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
        # Fetch H1 data for H4 resampling and price vs EMA200
        df_h1 = fetch_with_fallback(asset_name, asset_config, "1h", "60d")
        if df_h1 is None or df_h1.empty:
            return None, None, None
        
        prices_h1 = df_h1['Close'].values
        
        # Resample to H4 (every 4 hours)
        num_h4 = len(prices_h1) // 4
        if num_h4 < 5:
            log.warning(f"⚠️ {asset_name}: Only {num_h4} H4 bars available")
        
        prices_h4 = np.array([np.mean(prices_h1[i*4:(i+1)*4]) for i in range(num_h4)])
        
        # Fetch M5 data for MACD calculation (need enough for crossover detection)
        df_m5 = fetch_with_fallback(asset_name, asset_config, "5m", "1d")  # 1 day of 5min data
        if df_m5 is None or df_m5.empty:
            log.warning(f"⚠️ {asset_name}: Cannot fetch M5 data for MACD")
            return prices_h4, prices_h1, None
        
        prices_m5 = df_m5['Close'].values
        
        return prices_h4, prices_h1, prices_m5
    except Exception as e:
        log.error(f"Error getting data for {asset_name}: {e}")
        return None, None, None

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
    """Calculate MACD line and Signal line"""
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

# ============ SIGNAL CONDITIONS WITH DETAILED LOGGING ============
def check_conditions(prices_h4, prices_h1, prices_m5, asset_name):
    if len(prices_h4) < 5 or len(prices_h1) < 20:
        return {"long": False, "short": False, "price": 0, 
                "long_details": {}, "short_details": {}}
    
    # Get most recent values (use last closed bar)
    close_h4 = prices_h4[-2] if len(prices_h4) >= 2 else prices_h4[-1]
    close_h1 = prices_h1[-2] if len(prices_h1) >= 2 else prices_h1[-1]
    current_price = close_h1
    
    # Calculate EMAs on H4 and H1
    ema200_h4 = calculate_ema(prices_h4, min(200, len(prices_h4) - 1))
    ema200_h1 = calculate_ema(prices_h1, min(200, len(prices_h1) - 1))
    
    # Calculate MACD on M5 data
    macd_line = None
    signal_line = None
    macd_value = "Insufficient M5 data"
    cross_occurred = False
    cross_type = None
    
    if prices_m5 is not None and len(prices_m5) >= 40:
        macd_line, signal_line = calculate_macd(prices_m5)
        
        if macd_line and signal_line and len(macd_line) >= 3:
            # Get last 3 bars to detect crossover
            macd_prev = macd_line[-3]
            macd_curr = macd_line[-2]
            signal_prev = signal_line[-3]
            signal_curr = signal_line[-2]
            
            # Check if MACD just crossed Signal (within last bar)
            long_cross = (macd_prev < signal_prev and macd_curr >= signal_curr)
            short_cross = (macd_prev > signal_prev and macd_curr <= signal_curr)
            
            # Check zero line conditions
            long_below_zero = (macd_curr <= 0 and signal_curr <= 0)
            short_above_zero = (macd_curr >= 0 and signal_curr >= 0)
            
            # Determine if a valid crossover occurred in the last bar
            if long_cross and long_below_zero:
                cross_occurred = True
                cross_type = "LONG"
                macd_value = f"MACD: ${macd_curr:.2f}, Signal: ${signal_curr:.2f} | Crossed BELOW zero in last 5min"
            elif short_cross and short_above_zero:
                cross_occurred = True
                cross_type = "SHORT"
                macd_value = f"MACD: ${macd_curr:.2f}, Signal: ${signal_curr:.2f} | Crossed ABOVE zero in last 5min"
            else:
                macd_value = f"MACD: ${macd_curr:.2f}, Signal: ${signal_curr:.2f} | No recent crossover"
    
    # ============ LONG CONDITIONS DETAILS ============
    long_details = {}
    
    # Condition 1: H4 Price > EMA200
    if ema200_h4:
        long_details["H4 Price > EMA200"] = {
            "passed": close_h4 > ema200_h4,
            "value": f"${close_h4:.2f} > ${ema200_h4:.2f}"
        }
    else:
        long_details["H4 Price > EMA200"] = {"passed": False, "value": "Insufficient data"}
    
    # Condition 2: H1 Price > EMA200
    if ema200_h1:
        long_details["H1 Price > EMA200"] = {
            "passed": close_h1 > ema200_h1,
            "value": f"${close_h1:.2f} > ${ema200_h1:.2f}"
        }
    else:
        long_details["H1 Price > EMA200"] = {"passed": False, "value": "Insufficient data"}
    
    # Condition 3: MACD cross below zero within last 5 minutes
    long_details["MACD crossed below zero (last 5min)"] = {
        "passed": cross_occurred and cross_type == "LONG",
        "value": macd_value
    }
    
    # ============ SHORT CONDITIONS DETAILS ============
    short_details = {}
    
    # Condition 1: H4 Price < EMA200
    if ema200_h4:
        short_details["H4 Price < EMA200"] = {
            "passed": close_h4 < ema200_h4,
            "value": f"${close_h4:.2f} < ${ema200_h4:.2f}"
        }
    else:
        short_details["H4 Price < EMA200"] = {"passed": False, "value": "Insufficient data"}
    
    # Condition 2: H1 Price < EMA200
    if ema200_h1:
        short_details["H1 Price < EMA200"] = {
            "passed": close_h1 < ema200_h1,
            "value": f"${close_h1:.2f} < ${ema200_h1:.2f}"
        }
    else:
        short_details["H1 Price < EMA200"] = {"passed": False, "value": "Insufficient data"}
    
    # Condition 3: MACD cross above zero within last 5 minutes
    short_details["MACD crossed above zero (last 5min)"] = {
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
        "cross_type": cross_type
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
def send_signal(asset_name, emoji, signal_type, price, details, cross_type):
    # Get the conditions that passed
    conditions_passed = []
    for condition, data in details.items():
        if data["passed"]:
            # Simplify MACD message for Telegram
            if "MACD" in condition:
                conditions_passed.append(f"  ✅ MACD {cross_type} crossover confirmed")
            else:
                conditions_passed.append(f"  ✅ {condition}")
    
    conditions_text = "\n".join(conditions_passed)
    
    message = f"""<b>{'🟢' if signal_type == 'LONG' else '🔴'} {signal_type} SIGNAL — {emoji} {asset_name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>Conditions met (3/3):</b>
{conditions_text}

<code>{'🚀 Consider LONG position' if signal_type == 'LONG' else '📉 Consider SHORT position'}</code>"""
    
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
    log.info("📊 TRADING SIGNAL BOT - 5-MINUTE MACD CROSSOVER DETECTION")
    log.info("=" * 70)
    log.info("LONG:  H4 > EMA200 + H1 > EMA200 + MACD crossed BELOW 0 (last 5min)")
    log.info("SHORT: H4 < EMA200 + H1 < EMA200 + MACD crossed ABOVE 0 (last 5min)")
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
        
        prices_h4, prices_h1, prices_m5 = get_asset_data(name, asset_config)
        
        if prices_h4 is None or len(prices_h4) < 5:
            log.warning(f"⚠️ {name} - Insufficient H4 data (got {len(prices_h4) if prices_h4 is not None else 0} bars)")
            continue
        
        if prices_h1 is None or len(prices_h1) < 20:
            log.warning(f"⚠️ {name} - Insufficient H1 data (got {len(prices_h1) if prices_h1 is not None else 0} bars)")
            continue
        
        if prices_m5 is None or len(prices_m5) < 40:
            log.warning(f"⚠️ {name} - Insufficient M5 data for MACD crossover (got {len(prices_m5) if prices_m5 is not None else 0} bars)")
            # Continue anyway, MACD will show as failed
        
        result = check_conditions(prices_h4, prices_h1, prices_m5, name)
        current_price = result.get("price", 0)
        
        # Display LONG conditions with exact values
        log.info("")
        long_all_met = log_long_conditions(name, result["long_details"], current_price)
        
        # Display SHORT conditions with exact values
        log.info("")
        short_all_met = log_short_conditions(name, result["short_details"], current_price)
        
        # Send LONG signal if all conditions met
        if long_all_met:
            log.info(f"\n🟢🔔 LONG SIGNAL ACTIVE for {name}!")
            if not check_cooldown(name, "LONG"):
                if send_signal(name, asset_config["emoji"], "LONG", current_price, result["long_details"], "LONG"):
                    save_cooldown(name, "LONG")
                    signals_sent += 1
            else:
                log.info(f"⏰ {name} LONG - cooldown active (1 hour)")
        
        # Send SHORT signal if all conditions met
        if short_all_met:
            log.info(f"\n🔴🔔 SHORT SIGNAL ACTIVE for {name}!")
            if not check_cooldown(name, "SHORT"):
                if send_signal(name, asset_config["emoji"], "SHORT", current_price, result["short_details"], "SHORT"):
                    save_cooldown(name, "SHORT")
                    signals_sent += 1
            else:
                log.info(f"⏰ {name} SHORT - cooldown active (1 hour)")
        
        if not long_all_met and not short_all_met:
            log.info(f"\n📊 {name} - No signal (conditions not met)")
        
        # Delay between assets to avoid rate limits
        time.sleep(2)
    
    log.info(f"\n{'='*70}")
    log.info(f"✅ Cycle complete. Sent {signals_sent} signal(s).")
    log.info(f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"{'='*70}\n")

if __name__ == "__main__":
    main()
