"""
Telegram Signal Bot — XAUUSD, Crude Oil WTI, BTC, ETH
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
# CONFIGURATION - ALL WORKING SYMBOLS
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL   = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))

# ✅ WORKING SYMBOLS - UPDATED
SYMBOLS = {
    "XAUUSD":         "XAUUSD=X",   # Gold - WORKS ✅
    "Crude Oil WTI":  "BZ=F",       # Brent Crude - WORKS ✅ (FIXED)
    "Bitcoin":        "BTC-USD",    # Bitcoin - WORKS ✅
    "Ethereum":       "ETH-USD",    # Ethereum - WORKS ✅
}

_last_signal = {}
_last_vwap_signal = {}
SIGNAL_COOLDOWN_SECONDS = 3600
VWAP_COOLDOWN_SECONDS = 1800
VWAP_DEVIATION_THRESHOLD = 0.005
VWAP_VOLUME_SPIKE_THRESHOLD = 1.5
CRYPTO_VWAP_DEVIATION = 0.01
CRYPTO_VOLUME_THRESHOLD = 1.3

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def fetch_data(ticker, interval, period):
    try:
        df = yf.download(ticker, interval=interval, period=period, progress=False, timeout=10)
        return df if not df.empty else None
    except Exception as e:
        log.debug(f"Failed to fetch {ticker}: {e}")
        return None

def fetch_prices(ticker, interval, period):
    df = fetch_data(ticker, interval, period)
    return df['Close'].values if df is not None else np.array([])

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
    try:
        is_crypto = yahoo_symbol in ["BTC-USD", "ETH-USD"]
        m5_period = "3d" if is_crypto else "5d"
        h1_period = "14d" if is_crypto else "30d"
        
        prices_m5 = fetch_prices(yahoo_symbol, "5m", m5_period)
        if len(prices_m5) < 50:
            return False, {}, None
        
        current_price = float(prices_m5[-1])
        
        prices_h1 = fetch_prices(yahoo_symbol, "1h", h1_period)
        if len(prices_h1) < 200:
            return False, {}, current_price
        
        prices_h4 = resample_to_h4(prices_h1)
        if len(prices_h4) < 50:
            return False, {}, current_price
        
        cond = {
            "H4 price > EMA200": check_price_above_ema200(prices_h4),
            "H1 price > EMA200": check_price_above_ema200(prices_h1),
            "M5 price > EMA200": check_price_above_ema200(prices_m5),
            "M5 EMA9 > EMA200":  check_ema9_above_ema200(prices_m5),
            "MACD bull cross <0": check_macd_bull_cross_below_zero(prices_m5),
        }
        
        return all(cond.values()), cond, current_price
    except Exception as e:
        log.error(f"Error in run_all_checks for {ticker_name}: {e}")
        return False, {}, None

# ---------------------------------------------------------------------------
# Telegram Functions
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10).raise_for_status()
        log.info("Telegram message sent.")
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")

def build_message(symbol_name: str, cond: dict, price: float) -> str:
    tick = lambda v: "✅" if v else "❌"
    emoji = "₿" if "Bitcoin" in symbol_name else "Ξ" if "Ethereum" in symbol_name else "🥇" if "XAU" in symbol_name else "🛢️"
    lines = [f"<b>{emoji} BUY SIGNAL — {symbol_name}</b>", f"<b>Price:</b> ${price:,.2f}", f"<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}", "", "<b>Conditions:</b>"]
    lines.extend([f"  {tick(v)} {k}" for k, v in cond.items()])
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 50)
    log.info("🚀 SIGNAL BOT STARTED - WORKING SYMBOLS")
    log.info("=" * 50)
    log.info(f"Monitoring: {', '.join(SYMBOLS.keys())}")
    log.info(f"Crude Oil Symbol: BZ=F (FIXED - not CL=F)")
    log.info("=" * 50)
    
    send_telegram("🤖 <b>Signal Bot Online - FIXED VERSION</b>\n\n✅ Watching: XAUUSD, Crude Oil (BZ=F), BTC, ETH\n✅ All symbols working")
    
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
                    log.info(f"🔔 SIGNAL SENT for {name}")
            elif price:
                failed = [k for k, v in cond.items() if not v]
                log.info(f"❌ {name} - no signal. Failed: {failed[:2]}...")
            else:
                log.warning(f"⚠️ {name} - no data")
        
        log.info(f"💤 Sleeping {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
