"""
Telegram Signal Bot - Long & Short Signals
Gold (XAUUSD), Crude Oil (CL=F), BTC, ETH

LONG Entry Conditions:
  1. Price > EMA200 on H4
  2. Price > EMA200 on H1
  3. Price > EMA200 on M5
  4. EMA9 > EMA200 on M5
  5. Price > EMA9 on M5
  6. MACD line crosses Signal line from BELOW, both < 0

SHORT Entry Conditions:
  1. Price < EMA200 on H4
  2. Price < EMA200 on H1
  3. Price < EMA200 on M5
  4. EMA9 < EMA200 on M5
  5. Price < EMA9 on M5
  6. MACD line crosses Signal line from ABOVE, both > 0
"""

import os
import logging
import requests
from datetime import datetime, timezone
import time
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============ CONFIGURATION ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

COOLDOWN_FILE = "/tmp/last_signal.txt"
SIGNAL_COOLDOWN = 3600  # 1 hour between signals per asset

# Yahoo Finance symbols
ASSETS = {
    "XAUUSD (Gold)": {
        "symbol": "XAUUSD=X",
        "fallback": "GC=F",
        "emoji": "🥇"
    },
    "Crude Oil WTI": {
        "symbol": "CL=F",
        "fallback": "BZ=F",
        "emoji": "🛢️"
    },
    "Bitcoin": {
        "symbol": "BTC-USD",
        "fallback": None,
        "emoji": "₿"
    },
    "Ethereum": {
        "symbol": "ETH-USD",
        "fallback": None,
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

# ============ YAHOO FINANCE DATA FETCHING ============
def fetch_yahoo_data(symbol, interval, period="5d"):
    """Fetch OHLCV data from Yahoo Finance"""
    try:
        import yfinance as yf
        
        # Add browser headers to avoid rate limiting
        ticker = yf.Ticker(symbol)
        
        # Download data
        df = ticker.history(period=period, interval=interval, progress=False)
        
        if df is not None and not df.empty:
            return df
    
    except Exception as e:
        log.error(f"Error fetching {symbol} @ {interval}: {e}")
    
    return None

def fetch_with_fallback(asset_name, asset_config, interval, period="5d"):
    """Try primary symbol first, then fallback"""
    # Try primary symbol
    symbol = asset_config["symbol"]
    df = fetch_yahoo_data(symbol, interval, period)
    
    if df is not None and not df.empty:
        return df, symbol
    
    # Try fallback if available
    fallback = asset_config.get("fallback")
    if fallback:
        log.info(f"Primary {symbol} failed for {asset_name}, trying fallback {fallback}")
        df = fetch_yahoo_data(fallback, interval, period)
        if df is not None and not df.empty:
            return df, fallback
    
    return None, None

# ============ TECHNICAL INDICATORS ============
def calculate_ema(prices, period):
    """Calculate Exponential Moving Average"""
    if len(prices) < period:
        return None
    
    multiplier = 2 / (period + 1)
    ema = prices[0]
    
    for price in prices[1:]:
        ema = (price - ema) * multiplier + ema
    
    return ema

def get_ema_series(prices, period):
    """Get full EMA series for condition checking"""
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
    if len(prices) < slow:
        return None, None
    
    # Calculate EMAs
    ema_fast_values = get_ema_series(prices, fast)
    ema_slow_values = get_ema_series(prices, slow)
    
    if len(ema_fast_values) < slow or len(ema_slow_values) < slow:
        return None, None
    
    # MACD Line = Fast EMA - Slow EMA
    macd_line = [ema_fast_values[i] - ema_slow_values[i] for i in range(len(ema_slow_values))]
    
    # Signal Line = EMA of MACD Line
    signal_line = get_ema_series(macd_line, signal)
    
    if len(signal_line) < 2:
        return None, None
    
    return macd_line, signal_line

def check_conditions(prices_h4, prices_h1, prices_m5):
    """Check all long and short conditions"""
    
    if len(prices_h4) < 200 or len(prices_h1) < 200 or len(prices_m5) < 200:
        log.warning(f"Insufficient data: H4={len(prices_h4)}, H1={len(prices_h1)}, M5={len(prices_m5)}")
        return {
            "long": False, "long_details": {}, "short": False, "short_details": {}
        }
    
    # Calculate EMAs
    ema200_h4 = calculate_ema(prices_h4, 200)
    ema200_h1 = calculate_ema(prices_h1, 200)
    ema200_m5 = calculate_ema(prices_m5, 200)
    ema9_m5 = calculate_ema(prices_m5, 9)
    
    # Get last values (most recent closed bar)
    close_h4 = prices_h4[-2] if len(prices_h4) >= 2 else prices_h4[-1]
    close_h1 = prices_h1[-2] if len(prices_h1) >= 2 else prices_h1[-1]
    close_m5 = prices_m5[-2] if len(prices_m5) >= 2 else prices_m5[-1]
    
    # MACD calculation
    macd_line, signal_line = calculate_macd(prices_m5)
    
    # Initialize results
    long_conditions = {}
    short_conditions = {}
    
    # Condition 1,2,3: Price vs EMA200
    if ema200_h4 is not None:
        long_conditions["H4 Price > EMA200"] = close_h4 > ema200_h4
        short_conditions["H4 Price < EMA200"] = close_h4 < ema200_h4
    else:
        long_conditions["H4 Price > EMA200"] = False
        short_conditions["H4 Price < EMA200"] = False
    
    if ema200_h1 is not None:
        long_conditions["H1 Price > EMA200"] = close_h1 > ema200_h1
        short_conditions["H1 Price < EMA200"] = close_h1 < ema200_h1
    else:
        long_conditions["H1 Price > EMA200"] = False
        short_conditions["H1 Price < EMA200"] = False
    
    if ema200_m5 is not None:
        long_conditions["M5 Price > EMA200"] = close_m5 > ema200_m5
        short_conditions["M5 Price < EMA200"] = close_m5 < ema200_m5
    else:
        long_conditions["M5 Price > EMA200"] = False
        short_conditions["M5 Price < EMA200"] = False
    
    # Condition 4: EMA9 vs EMA200
    if ema9_m5 is not None and ema200_m5 is not None:
        long_conditions["EMA9 > EMA200"] = ema9_m5 > ema200_m5
        short_conditions["EMA9 < EMA200"] = ema9_m5 < ema200_m5
    else:
        long_conditions["EMA9 > EMA200"] = False
        short_conditions["EMA9 < EMA200"] = False
    
    # Condition 5: Price vs EMA9
    if ema9_m5 is not None:
        long_conditions["Price > EMA9"] = close_m5 > ema9_m5
        short_conditions["Price < EMA9"] = close_m5 < ema9_m5
    else:
        long_conditions["Price > EMA9"] = False
        short_conditions["Price < EMA9"] = False
    
    # Condition 6: MACD crossover
    if macd_line is not None and signal_line is not None and len(macd_line) >= 3 and len(signal_line) >= 3:
        macd_prev = macd_line[-3]
        macd_curr = macd_line[-2]
        signal_prev = signal_line[-3]
        signal_curr = signal_line[-2]
        
        # Long: MACD crosses Signal from BELOW, both < 0
        long_macd_cross = (macd_prev < signal_prev and macd_curr >= signal_curr)
        long_macd_below_zero = (macd_curr < 0 and signal_curr < 0)
        long_conditions["MACD cross below zero (Long)"] = long_macd_cross and long_macd_below_zero
        
        # Short: MACD crosses Signal from ABOVE, both > 0
        short_macd_cross = (macd_prev > signal_prev and macd_curr <= signal_curr)
        short_macd_above_zero = (macd_curr > 0 and signal_curr > 0)
        short_conditions["MACD cross above zero (Short)"] = short_macd_cross and short_macd_above_zero
    else:
        long_conditions["MACD cross below zero (Long)"] = False
        short_conditions["MACD cross above zero (Short)"] = False
    
    # Check if all conditions are met
    long_signal = all(long_conditions.values())
    short_signal = all(short_conditions.values())
    
    return {
        "long": long_signal,
        "long_details": long_conditions,
        "short": short_signal,
        "short_details": short_conditions,
        "price": close_m5
    }

def get_asset_data(asset_name, asset_config):
    """Fetch all required timeframes for an asset"""
    try:
        # Fetch H4 data (using 1h and resampling)
        df_h1, used_symbol = fetch_with_fallback(asset_name, asset_config, "1h", "60d")
        if df_h1 is None or df_h1.empty:
            log.warning(f"Cannot fetch H1 data for {asset_name}")
            return None, None, None
        
        prices_h1 = df_h1['Close'].values
        
        # Resample to H4 (every 4 hours)
        num_h4 = len(prices_h1) // 4
        prices_h4 = np.array([np.mean(prices_h1[i*4:(i+1)*4]) for i in range(num_h4)])
        
        # Fetch M5 data
        df_m5, _ = fetch_with_fallback(asset_name, asset_config, "5m", "5d")
        if df_m5 is None or df_m5.empty:
            log.warning(f"Cannot fetch M5 data for {asset_name}")
            return None, None, None
        
        prices_m5 = df_m5['Close'].values
        
        log.info(f"✅ {asset_name} - H4:{len(prices_h4)} bars, H1:{len(prices_h1)} bars, M5:{len(prices_m5)} bars")
        
        return prices_h4, prices_h1, prices_m5
        
    except Exception as e:
        log.error(f"Error getting data for {asset_name}: {e}")
        return None, None, None

# ============ TELEGRAM FUNCTIONS ============
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

def build_long_message(asset_name, emoji, price, conditions):
    """Build long signal message"""
    checkmarks = "✅ " * sum(conditions.values())
    crosses = "❌ " * (len(conditions) - sum(conditions.values()))
    
    message = f"""<b>🟢 LONG ENTRY SIGNAL — {emoji} {asset_name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>All 6 Conditions Met:</b>
{chr(10).join([f"  {('✅' if v else '❌')} {k}" for k, v in conditions.items()])}

<code>🚀 Consider LONG position with proper risk management
📍 Stop loss below recent swing low
🎯 Take profit at 2:1 risk/reward ratio</code>"""
    
    return message

def build_short_message(asset_name, emoji, price, conditions):
    """Build short signal message"""
    message = f"""<b>🔴 SHORT ENTRY SIGNAL — {emoji} {asset_name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>All 6 Conditions Met:</b>
{chr(10).join([f"  {('✅' if v else '❌')} {k}" for k, v in conditions.items()])}

<code>📉 Consider SHORT position with proper risk management
📍 Stop loss above recent swing high
🎯 Take profit at 2:1 risk/reward ratio</code>"""
    
    return message

# ============ MAIN FUNCTION ============
def main():
    log.info("=" * 60)
    log.info("📊 TRADING SIGNAL BOT - LONG & SHORT SIGNALS")
    log.info("=" * 60)
    log.info(f"Monitoring: {', '.join(ASSETS.keys())}")
    log.info("LONG: Price > EMA200 (H4/H1/M5) + EMA9 > EMA200 + Price > EMA9 + MACD crossover below 0")
    log.info("SHORT: Price < EMA200 (H4/H1/M5) + EMA9 < EMA200 + Price < EMA9 + MACD crossover above 0")
    log.info("=" * 60)
    
    signals_sent = 0
    
    for name, asset_config in ASSETS.items():
        log.info(f"\n🔍 Analyzing {name}...")
        
        # Get data for all timeframes
        prices_h4, prices_h1, prices_m5 = get_asset_data(name, asset_config)
        
        if prices_h4 is None or len(prices_h4) < 50:
            log.warning(f"⚠️ {name} - Insufficient H4 data (need 50+ bars)")
            continue
        
        if prices_h1 is None or len(prices_h1) < 200:
            log.warning(f"⚠️ {name} - Insufficient H1 data (need 200+ bars)")
            continue
        
        if prices_m5 is None or len(prices_m5) < 200:
            log.warning(f"⚠️ {name} - Insufficient M5 data (need 200+ bars)")
            continue
        
        # Check all conditions
        result = check_conditions(prices_h4, prices_h1, prices_m5)
        current_price = result.get("price", 0)
        
        log.info(f"💰 Current Price: ${current_price:,.2f}")
        
        # Check for LONG signal
        if result["long"]:
            log.info(f"🟢 LONG signal detected for {name}!")
            
            if not check_cooldown(f"{name}_LONG"):
                message = build_long_message(name, asset_config["emoji"], current_price, result["long_details"])
                if send_telegram(message):
                    save_cooldown(f"{name}_LONG")
                    signals_sent += 1
                    log.info(f"✅ LONG signal sent for {name}")
            else:
                log.info(f"⏰ {name} LONG - cooldown active")
        else:
            # Log which conditions failed
            failed = [k for k, v in result["long_details"].items() if not v]
            if failed:
                log.info(f"📊 {name} LONG - missing: {', '.join(failed[:3])}")
            else:
                log.info(f"📊 {name} - no LONG signal")
        
        # Check for SHORT signal
        if result["short"]:
            log.info(f"🔴 SHORT signal detected for {name}!")
            
            if not check_cooldown(f"{name}_SHORT"):
                message = build_short_message(name, asset_config["emoji"], current_price, result["short_details"])
                if send_telegram(message):
                    save_cooldown(f"{name}_SHORT")
                    signals_sent += 1
                    log.info(f"✅ SHORT signal sent for {name}")
            else:
                log.info(f"⏰ {name} SHORT - cooldown active")
        else:
            # Log which conditions failed
            failed = [k for k, v in result["short_details"].items() if not v]
            if failed:
                log.info(f"📊 {name} SHORT - missing: {', '.join(failed[:3])}")
        
        # Delay between assets to avoid rate limits
        time.sleep(3)
    
    log.info(f"\n✅ Cycle complete. Sent {signals_sent} signal(s).")
    log.info("=" * 60)

if __name__ == "__main__":
    main()
