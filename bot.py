"""
Telegram Signal Bot — XAUUSD, Crude Oil WTI, BTC, ETH
FIXED: Rate limiting protection for crypto symbols
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

# ✅ WORKING SYMBOLS
SYMBOLS = {
    "XAUUSD":         "XAUUSD=X",   # Gold
    "Crude Oil WTI":  "BZ=F",       # Brent Crude
    "Bitcoin":        "BTC-USD",    # Bitcoin
    "Ethereum":       "ETH-USD",    # Ethereum
}

_last_signal = {}
SIGNAL_COOLDOWN_SECONDS = 3600

# Rate limiting protection - stagger crypto checks
_last_crypto_check = {}
CRYPTO_CHECK_INTERVAL = 60  # Check crypto only every 60 seconds

# ---------------------------------------------------------------------------
# Rate-limited data fetching
# ---------------------------------------------------------------------------
def fetch_with_retry(ticker, interval, period, max_retries=2):
    """Fetch data with retry logic and rate limiting protection"""
    for attempt in range(max_retries):
        try:
            # Add delay between retries
            if attempt > 0:
                time.sleep(2 ** attempt)  # Exponential backoff: 2s, 4s
            
            df = yf.download(ticker, interval=interval, period=period, progress=False, timeout=10)
            
            if df is not None and not df.empty:
                return df
            else:
                log.warning(f"Empty data for {ticker} (attempt {attempt+1})")
                
        except Exception as e:
            log.warning(f"Attempt {attempt+1} failed for {ticker}: {str(e)[:50]}")
            
            # If it's a rate limit error, wait longer
            if "Rate limit" in str(e) or "Too Many Requests" in str(e):
                time.sleep(5)
    
    return None

def fetch_prices(ticker, interval, period):
    """Fetch price data with rate limiting"""
    df = fetch_with_retry(ticker, interval, period)
    return df['Close'].values if df is not None else np.array([])

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
    if len(prices) < 201:
        return False
    ema200 = calculate_ema(prices, 200)
    if len(ema200) == 0:
        return False
    return prices[-2] > ema200[-2]

def check_ema9_above_ema200(prices):
    if len(prices) < 201:
        return False
    ema9 = calculate_ema(prices, 9)
    ema200 = calculate_ema(prices, 200)
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

def run_all_checks(ticker_name, yahoo_symbol):
    """Run all conditions with rate limit protection"""
    try:
        is_crypto = yahoo_symbol in ["BTC-USD", "ETH-USD"]
        
        # Stagger crypto checks to avoid rate limiting
        if is_crypto:
            now = time.time()
            last_check = _last_crypto_check.get(yahoo_symbol, 0)
            if now - last_check < CRYPTO_CHECK_INTERVAL:
                log.debug(f"Skipping {ticker_name} - rate limited (last check {now-last_check:.0f}s ago)")
                return False, {}, None
            _last_crypto_check[yahoo_symbol] = now
        
        # Use shorter periods for crypto to avoid rate limits
        m5_period = "2d" if is_crypto else "5d"  # Reduced from 3d to 2d
        h1_period = "7d" if is_crypto else "30d"  # Reduced from 14d to 7d
        
        # Fetch data with retries
        prices_m5 = fetch_prices(yahoo_symbol, "5m", m5_period)
        if len(prices_m5) < 30:  # Reduced requirement from 50 to 30
            log.warning(f"Not enough M5 data for {ticker_name} (got {len(prices_m5)} bars)")
            return False, {}, None
        
        current_price = float(prices_m5[-1])
        
        # For crypto, use shorter lookback for H1
        prices_h1 = fetch_prices(yahoo_symbol, "1h", h1_period)
        if len(prices_h1) < 50:  # Reduced from 200 to 50 for crypto
            log.warning(f"Not enough H1 data for {ticker_name}")
            return False, {}, current_price
        
        # For H4, we need enough data
        if len(prices_h1) < 200 and not is_crypto:
            log.warning(f"Borderline H1 data for {ticker_name}")
        
        prices_h4 = resample_to_h4(prices_h1)
        if len(prices_h4) < 20:  # Reduced from 50 to 20
            log.warning(f"Not enough H4 data for {ticker_name}")
            return False, {}, current_price
        
        # Check conditions (skip EMA200 if not enough data)
        cond = {
            "H4 price > EMA200": check_price_above_ema200(prices_h4) if len(prices_h4) >= 200 else "insufficient data",
            "H1 price > EMA200": check_price_above_ema200(prices_h1) if len(prices_h1) >= 200 else "insufficient data",
            "M5 price > EMA200": check_price_above_ema200(prices_m5) if len(prices_m5) >= 200 else check_price_above_ema200(prices_m5[-100:]),  # Use last 100 bars
            "M5 EMA9 > EMA200":  check_ema9_above_ema200(prices_m5),
            "MACD bull cross <0": check_macd_bull_cross_below_zero(prices_m5),
        }
        
        # Convert any string results to False for triggering
        cond = {k: v if isinstance(v, bool) else False for k, v in cond.items()}
        
        triggered = all(cond.values())
        return triggered, cond, current_price
        
    except Exception as e:
        log.error(f"Error in run_all_checks for {ticker_name}: {e}")
        return False, {}, None

# ---------------------------------------------------------------------------
# Telegram Functions
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> None:
    """Send message to Telegram channel"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("✅ Telegram message sent")
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")

def build_message(symbol_name: str, cond: dict, price: float) -> str:
    """Build formatted Telegram message for BUY signal"""
    tick = lambda v: "✅" if v else "❌"
    
    if "Bitcoin" in symbol_name:
        emoji = "₿"
        price_str = f"${price:,.2f}"
    elif "Ethereum" in symbol_name:
        emoji = "Ξ"
        price_str = f"${price:,.2f}"
    elif "XAU" in symbol_name:
        emoji = "🥇"
        price_str = f"${price:,.2f}"
    else:
        emoji = "🛢️"
        price_str = f"${price:,.2f}"
    
    lines = [
        f"<b>{emoji} BUY SIGNAL — {symbol_name}</b>",
        f"<b>Price:</b> {price_str}",
        f"<b>Time (UTC):</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "<b>Conditions met:</b>",
    ]
    lines.extend([f"  {tick(v)} {k}" for k, v in cond.items()])
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 55)
    log.info("🚀 SIGNAL BOT STARTED - RATE LIMIT PROTECTION ENABLED")
    log.info("=" * 55)
    log.info(f"📊 Monitoring: {', '.join(SYMBOLS.keys())}")
    log.info(f"⏱️  Check interval: {CHECK_INTERVAL} seconds")
    log.info(f"🛡️  Crypto rate limit: {CRYPTO_CHECK_INTERVAL} seconds between checks")
    log.info("=" * 55)
    
    # Send startup message
    try:
        startup_msg = f"""🤖 <b>Signal Bot Online - RATE LIMIT FIXED</b>

📊 <b>Monitoring:</b>
• 🥇 XAUUSD (Gold)
• 🛢️ Crude Oil WTI (BZ=F)
• ₿ Bitcoin (BTC-USD)
• Ξ Ethereum (ETH-USD)

⚙️ <b>Protections:</b>
• Rate limiting protection enabled
• Automatic retry on failures
• Staggered crypto checks

✅ Bot is active!"""
        send_telegram(startup_msg)
        log.info("✅ Startup message sent to Telegram")
    except Exception as e:
        log.error(f"Failed to send startup message: {e}")
    
    # Main loop
    while True:
        for name, ticker in SYMBOLS.items():
            log.info(f"📊 Checking {name} ({ticker})...")
            triggered, cond, price = run_all_checks(name, ticker)
            
            if triggered and price:
                now = datetime.now(timezone.utc)
                last = _last_signal.get(name)
                if last is None or (now - last).total_seconds() >= SIGNAL_COOLDOWN_SECONDS:
                    send_telegram(build_message(name, cond, price))
                    _last_signal[name] = now
                    log.info(f"🔔 SIGNAL SENT for {name} at ${price:,.2f}")
                else:
                    remaining = SIGNAL_COOLDOWN_SECONDS - (now - last).total_seconds()
                    log.info(f"⏰ {name} signal suppressed (cooldown: {remaining:.0f}s)")
            elif price:
                failed = [k for k, v in cond.items() if not v]
                if failed:
                    log.info(f"❌ {name} - no signal. Failed: {failed[0]}, {failed[1] if len(failed)>1 else ''}")
            else:
                log.warning(f"⚠️ {name} - temporarily unavailable (rate limit)")
            
            # Small delay between symbols to avoid rate limits
            time.sleep(3)
        
        log.info(f"💤 Cycle complete. Sleeping {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
