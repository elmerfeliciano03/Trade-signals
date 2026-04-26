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

SYMBOLS = {
    "XAUUSD":       "GC=F",
    "Crude Oil WTI": "CL=F",
}

_last_signal = {}
SIGNAL_COOLDOWN_SECONDS = 3600

# ---------------------------------------------------------------------------
# EMA calculation
# ---------------------------------------------------------------------------
def calculate_ema(prices, period):
    """Calculate EMA using numpy"""
    ema = np.zeros(len(prices))
    multiplier = 2 / (period + 1)
    
    # Start with SMA for first value
    ema[period-1] = np.mean(prices[:period])
    
    for i in range(period, len(prices)):
        ema[i] = (prices[i] - ema[i-1]) * multiplier + ema[i-1]
    
    return ema

def fetch_and_validate(ticker, interval, period):
    """Fetch data and return price array"""
    df = yf.download(ticker, interval=interval, period=period, progress=False)
    if df.empty:
        raise ValueError(f"No data for {ticker}")
    return df['Close'].values

def get_current_price(ticker):
    """Get current price"""
    df = yf.download(ticker, period="1d", interval="1m", progress=False)
    if df.empty:
        raise ValueError(f"No price data for {ticker}")
    return float(df['Close'].iloc[-1])

# ---------------------------------------------------------------------------
# Condition checks
# ---------------------------------------------------------------------------
def check_price_above_ema200(prices):
    """Last closed bar close > EMA200"""
    if len(prices) < 201:
        return False
    ema200 = calculate_ema(prices, 200)
    return prices[-2] > ema200[-2]

def check_ema9_above_ema200(prices):
    """EMA9 > EMA200"""
    if len(prices) < 201:
        return False
    ema9 = calculate_ema(prices, 9)
    ema200 = calculate_ema(prices, 200)
    return ema9[-2] > ema200[-2]

def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD"""
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    return macd_line, signal_line

def check_macd_bull_cross_below_zero(prices):
    """MACD crosses Signal from below, both below zero"""
    if len(prices) < 35:
        return False
    
    macd_line, signal_line = calculate_macd(prices)
    
    mc_prev = macd_line[-3]
    sc_prev = signal_line[-3]
    mc_curr = macd_line[-2]
    sc_curr = signal_line[-2]
    
    cross_up = mc_prev < sc_prev and mc_curr >= sc_curr
    below_zero = mc_curr < 0 and sc_curr < 0
    
    return cross_up and below_zero

def run_all_checks(ticker):
    """Run all conditions"""
    try:
        # Fetch M5 data
        df_m5 = yf.download(ticker, interval="5m", period="5d", progress=False)
        if df_m5.empty:
            return False, {}, None
        
        prices_m5 = df_m5['Close'].values
        current_price = float(prices_m5[-1])
        
        # Fetch H1 data
        df_h1 = yf.download(ticker, interval="1h", period="60d", progress=False)
        if df_h1.empty:
            return False, {}, None
        
        prices_h1 = df_h1['Close'].values
        
        # For H4, resample H1 data
        df_h4 = df_h1.resample('4H').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()
        
        prices_h4 = df_h4['Close'].values
        
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
        log.error(f"Error in run_all_checks: {e}")
        return False, {}, None

# ---------------------------------------------------------------------------
# Telegram functions (same as before)
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    log.info("Telegram message sent.")

def build_message(symbol_name: str, cond: dict, price: float) -> str:
    tick = lambda v: "✅" if v else "❌"
    lines = [
        f"<b>🟡 BUY SIGNAL — {symbol_name}</b>",
        f"<b>Price:</b> {price:.4f}",
        f"<b>Time (UTC):</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
        "",
        "<b>Conditions met:</b>",
    ] + [f"  {tick(v)} {k}" for k, v in cond.items()]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    log.info("Bot started. Checking every %d seconds.", CHECK_INTERVAL)
    
    # Send startup message
    try:
        send_telegram("🤖 <b>Signal bot online.</b>\nWatching: " + ", ".join(SYMBOLS.keys()))
    except Exception as e:
        log.error(f"Failed to send startup message: {e}")

    while True:
        for name, ticker in SYMBOLS.items():
            try:
                log.info("Checking %s (%s)…", name, ticker)
                triggered, cond, price = run_all_checks(ticker)

                if triggered and price:
                    now = datetime.now(timezone.utc)
                    last = _last_signal.get(name)
                    if last is None or (now - last).total_seconds() >= SIGNAL_COOLDOWN_SECONDS:
                        msg = build_message(name, cond, price)
                        send_telegram(msg)
                        _last_signal[name] = now
                        log.info("Signal sent for %s.", name)
                    else:
                        log.info("Signal for %s suppressed (cooldown).", name)
                elif price:
                    log.info("%s — no signal.", name)
                else:
                    log.info("%s — check failed.", name)

            except Exception as exc:
                log.error("Error checking %s: %s", name, exc)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
