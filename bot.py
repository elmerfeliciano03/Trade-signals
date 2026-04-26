"""
Telegram Signal Bot — XAUUSD, Crude Oil WTI, BTC, ETH
Checks every 5 minutes for all EA entry conditions and sends a Telegram alert.

Conditions (BUY signal):
  1. Price > EMA200 on H4
  2. Price > EMA200 on H1
  3. Price > EMA200 on M5
  4. EMA9 > EMA200 on M5
  5. MACD line crosses Signal from below, both < 0 (on M5)
  
ADDITIONAL: VWAP Spike Signal
  - Sudden price deviation from VWAP with volume confirmation
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
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL   = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))

# WORKING SYMBOLS for Yahoo Finance
SYMBOLS = {
    "XAUUSD":         "XAUUSD=X",   # Gold (forex pair - works weekends)
    "Crude Oil WTI":  "CL=F",       # WTI Crude (futures - better on weekdays)
    "Bitcoin":        "BTC-USD",    # Bitcoin (crypto - works 24/7)
    "Ethereum":       "ETH-USD",    # Ethereum (crypto - works 24/7)
}

_last_signal = {}
_last_vwap_signal = {}  # Separate tracking for VWAP signals
SIGNAL_COOLDOWN_SECONDS = 3600  # max one alert per symbol per hour
VWAP_COOLDOWN_SECONDS = 1800    # 30 min cooldown for VWAP spikes

# VWAP Configuration
VWAP_DEVIATION_THRESHOLD = 0.005  # 0.5% deviation from VWAP triggers alert
VWAP_VOLUME_SPIKE_THRESHOLD = 1.5  # 50% above average volume

# Crypto-specific adjustments (more volatile)
CRYPTO_VWAP_DEVIATION = 0.01      # 1% for crypto (more tolerant)
CRYPTO_VOLUME_THRESHOLD = 1.3     # 1.3x for crypto

# ---------------------------------------------------------------------------
# EMA calculation using numpy
# ---------------------------------------------------------------------------
def calculate_ema(prices, period):
    """Calculate EMA using numpy"""
    if len(prices) < period:
        return np.array([])
    
    ema = np.zeros(len(prices))
    multiplier = 2 / (period + 1)
    
    # Start with SMA for first value
    ema[period-1] = np.mean(prices[:period])
    
    for i in range(period, len(prices)):
        ema[i] = (prices[i] - ema[i-1]) * multiplier + ema[i-1]
    
    return ema

def fetch_data(ticker, interval, period):
    """Fetch OHLCV data with volume"""
    try:
        df = yf.download(ticker, interval=interval, period=period, progress=False, timeout=10)
        if df.empty:
            raise ValueError(f"No data for {ticker}")
        return df
    except Exception as e:
        log.error(f"Failed to fetch {ticker} @ {interval}: {e}")
        return None

def fetch_prices(ticker, interval, period):
    """Fetch price data and return close prices as numpy array"""
    df = fetch_data(ticker, interval, period)
    if df is None or df.empty:
        return np.array([])
    return df['Close'].values

def fetch_ohlcv(ticker, interval, period):
    """Fetch OHLCV data with volume for VWAP calculation"""
    df = fetch_data(ticker, interval, period)
    if df is None or df.empty:
        return None, None, None, None, None
    
    return (
        df['Open'].values,
        df['High'].values,
        df['Low'].values,
        df['Close'].values,
        df['Volume'].values
    )

# ---------------------------------------------------------------------------
# VWAP Calculation and Spike Detection
# ---------------------------------------------------------------------------
def calculate_vwap(high, low, close, volume):
    """Calculate VWAP (Volume Weighted Average Price)"""
    if len(high) == 0 or len(volume) == 0:
        return np.array([])
    
    # Typical Price = (High + Low + Close) / 3
    typical_price = (high + low + close) / 3
    
    # Cumulative (Typical Price * Volume)
    cum_typical_volume = np.cumsum(typical_price * volume)
    
    # Cumulative Volume
    cum_volume = np.cumsum(volume)
    
    # VWAP = Cumulative TV / Cumulative Volume
    vwap = cum_typical_volume / cum_volume
    
    return vwap

def detect_vwap_spike(ticker, lookback_bars=20, deviation_threshold=VWAP_DEVIATION_THRESHOLD, volume_spike_threshold=VWAP_VOLUME_SPIKE_THRESHOLD):
    """
    Detect sudden VWAP spike with volume confirmation
    
    Returns:
    - spike_detected: bool
    - deviation: float (percentage deviation from VWAP)
    - volume_ratio: float (current volume vs average)
    - current_vwap: float
    - current_price: float
    """
    try:
        # Adjust thresholds for crypto
        is_crypto = ticker in ["BTC-USD", "ETH-USD"]
        if is_crypto:
            deviation_threshold = CRYPTO_VWAP_DEVIATION
            volume_spike_threshold = CRYPTO_VOLUME_THRESHOLD
            log.debug(f"Crypto detected {ticker}, using adjusted thresholds (dev: {deviation_threshold:.1%}, vol: {volume_spike_threshold}x)")
        
        # Fetch M1 or M5 data for VWAP (intraday)
        o, h, l, c, v = fetch_ohlcv(ticker, "5m", "3d")
        
        if o is None or len(o) < lookback_bars:
            log.warning(f"Not enough data for VWAP calculation on {ticker}")
            return False, 0, 0, 0, 0
        
        # Calculate VWAP
        vwap = calculate_vwap(h, l, c, v)
        
        if len(vwap) < 2:
            return False, 0, 0, 0, 0
        
        # Current values
        current_price = c[-1]
        current_vwap = vwap[-1]
        current_volume = v[-1]
        
        # Calculate percentage deviation from VWAP
        deviation = abs((current_price - current_vwap) / current_vwap)
        
        # Calculate average volume (excluding current bar)
        avg_volume = np.mean(v[-lookback_bars-1:-1])
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
        
        # Calculate how fast price moved (price velocity)
        if len(c) >= 5:
            price_velocity = abs(c[-1] - c[-3]) / c[-3]  # 2-bar movement
        else:
            price_velocity = 0
        
        # Spike conditions:
        # 1. Price deviates significantly from VWAP
        # 2. Volume confirms the move (spike)
        # 3. Rapid price movement
        spike_conditions = {
            "deviation_threshold_met": deviation >= deviation_threshold,
            "volume_spike_met": volume_ratio >= volume_spike_threshold,
            "price_velocity_met": price_velocity >= deviation_threshold / 2
        }
        
        spike_detected = all(spike_conditions.values())
        
        log.debug(f"VWAP Check for {ticker} - Deviation: {deviation:.3%}, Volume Ratio: {volume_ratio:.2f}, Velocity: {price_velocity:.3%}")
        
        return spike_detected, deviation, volume_ratio, current_vwap, current_price
        
    except Exception as e:
        log.error(f"Error detecting VWAP spike for {ticker}: {e}")
        return False, 0, 0, 0, 0

# ---------------------------------------------------------------------------
# Condition checks
# ---------------------------------------------------------------------------
def check_price_above_ema200(prices):
    """Last closed bar close > EMA200"""
    if len(prices) < 201:
        return False
    ema200 = calculate_ema(prices, 200)
    if len(ema200) == 0:
        return False
    return prices[-2] > ema200[-2]

def check_ema9_above_ema200(prices):
    """EMA9 > EMA200"""
    if len(prices) < 201:
        return False
    ema9 = calculate_ema(prices, 9)
    ema200 = calculate_ema(prices, 200)
    if len(ema9) == 0 or len(ema200) == 0:
        return False
    return ema9[-2] > ema200[-2]

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD line and signal line"""
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
    """MACD crosses Signal from below, both lines below zero"""
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
    """Resample hourly prices to 4-hour prices using simple averaging"""
    if len(prices_h1) < 4:
        return np.array([])
    
    # Group every 4 hours
    num_h4_bars = len(prices_h1) // 4
    prices_h4 = np.zeros(num_h4_bars)
    
    for i in range(num_h4_bars):
        start_idx = i * 4
        end_idx = start_idx + 4
        prices_h4[i] = np.mean(prices_h1[start_idx:end_idx])
    
    return prices_h4

def run_all_checks(ticker):
    """Run all conditions and return (signal_triggered, conditions_dict, current_price)"""
    try:
        # For crypto, use shorter periods due to 24/7 trading
        is_crypto = ticker in ["BTC-USD", "ETH-USD"]
        m5_period = "3d" if is_crypto else "5d"
        h1_period = "14d" if is_crypto else "30d"
        
        # Fetch M5 data
        prices_m5 = fetch_prices(ticker, "5m", m5_period)
        if len(prices_m5) < 50:
            log.warning(f"Not enough M5 data for {ticker}")
            return False, {}, None
        
        current_price = float(prices_m5[-1])
        
        # Fetch H1 data for both H1 and H4
        prices_h1 = fetch_prices(ticker, "1h", h1_period)
        if len(prices_h1) < 200:
            log.warning(f"Not enough H1 data for {ticker}")
            return False, {}, current_price
        
        # Create H4 data by resampling H1
        prices_h4 = resample_to_h4(prices_h1)
        if len(prices_h4) < 50:
            log.warning(f"Not enough H4 data for {ticker}")
            return False, {}, current_price
        
        # Check all conditions
        cond = {
            "H4 price > EMA200": check_price_above_ema200(prices_h4),
            "H1 price > EMA200": check_price_above_ema200(prices_h1),
            "M5 price > EMA200": check_price_above_ema200(prices_m5),
            "M5 EMA9 > EMA200":  check_ema9_above_ema200(prices_m5),
            "MACD bull cross <0": check_macd_bull_cross_below_zero(prices_m5),
        }
        
        triggered = all(cond.values())
        return triggered, cond, current_price
        
    except Exception as e:
        log.error(f"Error in run_all_checks for {ticker}: {e}")
        return False, {}, None

def run_vwap_check(ticker):
    """Run VWAP spike detection separately"""
    try:
        spike_detected, deviation, volume_ratio, vwap, price = detect_vwap_spike(ticker)
        return spike_detected, deviation, volume_ratio, vwap, price
    except Exception as e:
        log.error(f"Error in VWAP check for {ticker}: {e}")
        return False, 0, 0, 0, 0

# ---------------------------------------------------------------------------
# Telegram functions
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> None:
    """Send message to Telegram channel"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("Telegram message sent successfully.")
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")

def build_message(symbol_name: str, cond: dict, price: float) -> str:
    """Build formatted Telegram message for BUY signal"""
    tick = lambda v: "✅" if v else "❌"
    
    # Add crypto emoji if applicable
    emoji = "🪙" if "Bitcoin" in symbol_name or "Ethereum" in symbol_name else "🟡"
    if "Bitcoin" in symbol_name:
        emoji = "₿"
    elif "Ethereum" in symbol_name:
        emoji = "Ξ"
    
    lines = [
        f"<b>{emoji} BUY SIGNAL — {symbol_name}</b>",
        f"<b>Price:</b> ${price:.2f}" if "BTC" in symbol_name or "ETH" in symbol_name else f"<b>Price:</b> {price:.4f}",
        f"<b>Time (UTC):</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "<b>Conditions met:</b>",
    ]
    
    for k, v in cond.items():
        lines.append(f"  {tick(v)} {k}")
    
    return "\n".join(lines)

def build_vwap_message(symbol_name: str, deviation: float, volume_ratio: float, vwap: float, price: float) -> str:
    """Build formatted Telegram message for VWAP spike alert"""
    direction = "ABOVE" if price > vwap else "BELOW"
    emoji = "📈" if price > vwap else "📉"
    
    # Add crypto indicator
    is_crypto = "Bitcoin" in symbol_name or "Ethereum" in symbol_name
    crypto_note = "\n<i>⚠️ Crypto is more volatile - consider wider stops</i>" if is_crypto else ""
    
    lines = [
        f"<b>{emoji} VWAP SPIKE ALERT — {symbol_name}</b>",
        f"<b>Price:</b> ${price:.2f}" if "BTC" in symbol_name or "ETH" in symbol_name else f"<b>Price:</b> {price:.4f}",
        f"<b>VWAP:</b> ${vwap:.2f}" if "BTC" in symbol_name or "ETH" in symbol_name else f"<b>VWAP:</b> {vwap:.4f}",
        f"<b>Deviation:</b> {deviation:.2%} {direction} VWAP",
        f"<b>Volume Spike:</b> {volume_ratio:.1f}x average",
        f"<b>Time (UTC):</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "<b>⚠️ Potential momentum move detected!</b>",
        "<i>Consider watching for continuation or reversal.</i>",
        crypto_note
    ]
    
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 50)
    log.info("Bot started. Checking every %d seconds.", CHECK_INTERVAL)
    log.info("Monitoring symbols: %s", ", ".join(SYMBOLS.keys()))
    log.info("VWAP Spike Detection: ENABLED")
    log.info("  - Forex/Commodities: 0.5% deviation, 1.5x volume")
    log.info("  - Crypto (BTC/ETH): 1.0% deviation, 1.3x volume")
    log.info("=" * 50)
    
    # Send startup message
    try:
        startup_msg = f"""🤖 <b>Signal Bot Online</b>

📊 <b>Watching:</b>
• XAUUSD (Gold)
• Crude Oil WTI
• ₿ Bitcoin (BTC-USD)
• Ξ Ethereum (ETH-USD)

⏱️ <b>Settings:</b>
• Interval: {CHECK_INTERVAL} seconds
• Signal cooldown: {SIGNAL_COOLDOWN_SECONDS} seconds
• VWAP cooldown: {VWAP_COOLDOWN_SECONDS} seconds

📈 <b>VWAP Spike Detection:</b>
• Forex/Commodities: 0.5% deviation, 1.5x volume
• Crypto (BTC/ETH): 1.0% deviation, 1.3x volume

✅ Bot is active and monitoring..."""
        send_telegram(startup_msg)
    except Exception as e:
        log.error(f"Failed to send startup message: {e}")

    while True:
        for name, ticker in SYMBOLS.items():
            # Check for regular BUY signal
            try:
                log.info("📊 Checking %s (%s) for BUY signal…", name, ticker)
                triggered, cond, price = run_all_checks(ticker)

                if triggered and price:
                    now = datetime.now(timezone.utc)
                    last = _last_signal.get(name)
                    
                    if last is None or (now - last).total_seconds() >= SIGNAL_COOLDOWN_SECONDS:
                        msg = build_message(name, cond, price)
                        send_telegram(msg)
                        _last_signal[name] = now
                        log.info("🔔 BUY SIGNAL SENT for %s at price %.4f", name, price)
                    else:
                        remaining = SIGNAL_COOLDOWN_SECONDS - (now - last).total_seconds()
                        log.info("⏰ BUY signal for %s suppressed (cooldown: %.0f seconds remaining)", name, remaining)
                elif price:
                    # Log which conditions failed
                    failed = [k for k, v in cond.items() if not v]
                    log.info("❌ %s — no BUY signal. Failed conditions: %s", name, failed)
                else:
                    log.warning("⚠️ %s — check failed (no price data)", name)

            except Exception as exc:
                log.error("💥 Error checking %s for BUY signal: %s", name, exc)
            
            # Check for VWAP spike signal
            try:
                log.info("📈 Checking %s (%s) for VWAP spike…", name, ticker)
                vwap_triggered, deviation, volume_ratio, vwap, price = run_vwap_check(ticker)
                
                if vwap_triggered and price and vwap:
                    now = datetime.now(timezone.utc)
                    last_vwap = _last_vwap_signal.get(name)
                    
                    if last_vwap is None or (now - last_vwap).total_seconds() >= VWAP_COOLDOWN_SECONDS:
                        msg = build_vwap_message(name, deviation, volume_ratio, vwap, price)
                        send_telegram(msg)
                        _last_vwap_signal[name] = now
                        log.info("🔔 VWAP SPIKE ALERT for %s (deviation: %.2f%%, volume: %.1fx)", name, deviation*100, volume_ratio)
                    else:
                        remaining = VWAP_COOLDOWN_SECONDS - (now - last_vwap).total_seconds()
                        log.info("⏰ VWAP alert for %s suppressed (cooldown: %.0f seconds remaining)", name, remaining)
                elif vwap_triggered:
                    log.info("❌ %s — no VWAP spike (insufficient data)", name)
                    
            except Exception as exc:
                log.error("💥 Error checking %s for VWAP spike: %s", name, exc)

        # Wait before next check
        log.info("💤 Sleeping for %d seconds...", CHECK_INTERVAL)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
