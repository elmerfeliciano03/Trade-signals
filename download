"""
Telegram Signal Bot — XAUUSD & Crude Oil WTI
Checks every 5 minutes for all EA entry conditions and sends a Telegram alert.

Conditions (BUY signal):
  1. Price > EMA200 on H4
  2. Price > EMA200 on H1
  3. Price > EMA200 on M5
  4. EMA9 > EMA200 on M5
  5. MACD line crosses Signal from below, both < 0 (on M5)
"""

import os
import time
import logging
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — set via environment variables (see .env.example)
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL   = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))  # 5 min

SYMBOLS = {
    "XAUUSD":       "GC=F",   # Gold futures
    "Crude Oil WTI": "CL=F",  # WTI Crude futures
}

# Track last signal time per symbol so we don't spam
_last_signal: dict[str, datetime] = {}
SIGNAL_COOLDOWN_SECONDS = 3600  # max one alert per symbol per hour


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def fetch_candles(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """Download OHLCV data from Yahoo Finance and return a clean DataFrame."""
    df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data returned for {ticker} @ {interval}")
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    return df


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    macd_line   = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


# ---------------------------------------------------------------------------
# Condition checks
# ---------------------------------------------------------------------------
def check_price_above_ema200(df: pd.DataFrame) -> bool:
    """Last closed bar close > EMA200."""
    e200 = ema(df["close"], 200)
    return float(df["close"].iloc[-2]) > float(e200.iloc[-2])


def check_ema9_above_ema200_m5(df_m5: pd.DataFrame) -> bool:
    e9   = ema(df_m5["close"], 9)
    e200 = ema(df_m5["close"], 200)
    return float(e9.iloc[-2]) > float(e200.iloc[-2])


def check_macd_bull_cross_below_zero(df_m5: pd.DataFrame) -> bool:
    """MACD crosses Signal from below, both lines below zero."""
    macd_line, signal_line = macd(df_m5["close"])

    mc  = float(macd_line.iloc[-2]);  sc  = float(signal_line.iloc[-2])
    mp  = float(macd_line.iloc[-3]);  sp  = float(signal_line.iloc[-3])

    cross_up   = mp < sp and mc >= sc
    below_zero = mc < 0.0 and sc < 0.0
    return cross_up and below_zero


def run_all_checks(ticker: str) -> tuple[bool, dict]:
    """
    Returns (signal_triggered, detail_dict).
    detail_dict contains True/False per condition for the alert message.
    """
    df_m5 = fetch_candles(ticker, "5m",  "5d")
    df_h1 = fetch_candles(ticker, "1h",  "60d")
    df_h4 = fetch_candles(ticker, "1h",  "60d")   # Yahoo doesn't have 4h; we resample
    df_h4 = (
        df_h4.resample("4h", on=df_h4.index.name if df_h4.index.name else "Datetime"
                       if "Datetime" in str(df_h4.index) else None)
        .agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
        .dropna()
    ) if False else _resample_h4(df_h4)

    cond = {
        "H4 price > EMA200": check_price_above_ema200(df_h4),
        "H1 price > EMA200": check_price_above_ema200(df_h1),
        "M5 price > EMA200": check_price_above_ema200(df_m5),
        "M5 EMA9 > EMA200":  check_ema9_above_ema200_m5(df_m5),
        "MACD bull cross <0": check_macd_bull_cross_below_zero(df_m5),
    }

    current_price = float(df_m5["close"].iloc[-1])
    triggered = all(cond.values())
    return triggered, cond, current_price


def _resample_h4(df_h1: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h data into 4h bars."""
    df = df_h1.copy()
    df.index = pd.to_datetime(df.index)
    return df.resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    ).dropna()


# ---------------------------------------------------------------------------
# Telegram messenger
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
    send_telegram("🤖 <b>Signal bot online.</b>\nWatching: " + ", ".join(SYMBOLS.keys()))

    while True:
        for name, ticker in SYMBOLS.items():
            try:
                log.info("Checking %s (%s)…", name, ticker)
                triggered, cond, price = run_all_checks(ticker)

                if triggered:
                    now = datetime.now(timezone.utc)
                    last = _last_signal.get(name)
                    if last is None or (now - last).total_seconds() >= SIGNAL_COOLDOWN_SECONDS:
                        msg = build_message(name, cond, price)
                        send_telegram(msg)
                        _last_signal[name] = now
                        log.info("Signal sent for %s.", name)
                    else:
                        log.info("Signal for %s suppressed (cooldown).", name)
                else:
                    log.info("%s — no signal. Conditions: %s", name,
                             {k: v for k, v in cond.items()})

            except Exception as exc:
                log.error("Error checking %s: %s", name, exc)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
