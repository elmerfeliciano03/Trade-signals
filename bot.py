"""
Telegram Signal Bot — XAUUSD, Crude Oil WTI, BTCUSD, ETHUSD
Checks every 5 minutes for BUY and SELL conditions.

BUY conditions (all must be true):
  1. Price > EMA200 on H4
  2. Price > EMA200 on H1
  3. Price > EMA200 on M5
  4. EMA9 > EMA200 on M5
  5. MACD line crosses Signal from below, both lines < 0 (M5)

SELL conditions (all must be true):
  1. Price < EMA200 on H4
  2. Price < EMA200 on H1
  3. Price < EMA200 on M5
  4. EMA9 < EMA200 on M5
  5. MACD line crosses Signal from above, both lines > 0 (M5)
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
# Config — set via environment variables
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL   = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))  # 5 min

SYMBOLS = {
    "XAUUSD":        "GC=F",     # Gold futures
    "Crude Oil WTI": "CL=F",     # WTI Crude futures
    "BTCUSD":        "BTC-USD",  # Bitcoin
    "ETHUSD":        "ETH-USD",  # Ethereum
}

# Cooldown: max one alert per symbol+direction per hour
SIGNAL_COOLDOWN_SECONDS = 3600
_last_signal: dict[str, datetime] = {}  # key = "SYMBOL_BUY" or "SYMBOL_SELL"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def fetch_candles(ticker: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data returned for {ticker} @ {interval}")
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    return df


def _resample_h4(df_h1: pd.DataFrame) -> pd.DataFrame:
    df = df_h1.copy()
    df.index = pd.to_datetime(df.index)
    return df.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    macd_line   = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


# ---------------------------------------------------------------------------
# Condition checks — BUY
# ---------------------------------------------------------------------------
def price_above_ema200(df: pd.DataFrame) -> bool:
    e200 = ema(df["close"], 200)
    return float(df["close"].iloc[-2]) > float(e200.iloc[-2])


def ema9_above_ema200(df_m5: pd.DataFrame) -> bool:
    e9   = ema(df_m5["close"], 9)
    e200 = ema(df_m5["close"], 200)
    return float(e9.iloc[-2]) > float(e200.iloc[-2])


def macd_bull_cross_below_zero(df_m5: pd.DataFrame) -> bool:
    """MACD crosses Signal from below, both lines below zero."""
    ml, sl = macd(df_m5["close"])
    mc, sc = float(ml.iloc[-2]), float(sl.iloc[-2])
    mp, sp = float(ml.iloc[-3]), float(sl.iloc[-3])
    return (mp < sp and mc >= sc) and (mc < 0.0 and sc < 0.0)


# ---------------------------------------------------------------------------
# Condition checks — SELL
# ---------------------------------------------------------------------------
def price_below_ema200(df: pd.DataFrame) -> bool:
    e200 = ema(df["close"], 200)
    return float(df["close"].iloc[-2]) < float(e200.iloc[-2])


def ema9_below_ema200(df_m5: pd.DataFrame) -> bool:
    e9   = ema(df_m5["close"], 9)
    e200 = ema(df_m5["close"], 200)
    return float(e9.iloc[-2]) < float(e200.iloc[-2])


def macd_bear_cross_above_zero(df_m5: pd.DataFrame) -> bool:
    """MACD crosses Signal from above, both lines above zero."""
    ml, sl = macd(df_m5["close"])
    mc, sc = float(ml.iloc[-2]), float(sl.iloc[-2])
    mp, sp = float(ml.iloc[-3]), float(sl.iloc[-3])
    return (mp > sp and mc <= sc) and (mc > 0.0 and sc > 0.0)


# ---------------------------------------------------------------------------
# Run all checks for one symbol
# ---------------------------------------------------------------------------
def run_checks(ticker: str) -> tuple[dict, dict, float]:
    """Returns (buy_conditions, sell_conditions, current_price)."""
    df_m5 = fetch_candles(ticker, "5m", "5d")
    df_h1 = fetch_candles(ticker, "1h", "60d")
    df_h4 = _resample_h4(fetch_candles(ticker, "1h", "60d"))

    buy_cond = {
        "H4 price > EMA200":   price_above_ema200(df_h4),
        "H1 price > EMA200":   price_above_ema200(df_h1),
        "M5 price > EMA200":   price_above_ema200(df_m5),
        "M5 EMA9 > EMA200":    ema9_above_ema200(df_m5),
        "MACD bull cross < 0": macd_bull_cross_below_zero(df_m5),
    }

    sell_cond = {
        "H4 price < EMA200":   price_below_ema200(df_h4),
        "H1 price < EMA200":   price_below_ema200(df_h1),
        "M5 price < EMA200":   price_below_ema200(df_m5),
        "M5 EMA9 < EMA200":    ema9_below_ema200(df_m5),
        "MACD bear cross > 0": macd_bear_cross_above_zero(df_m5),
    }

    current_price = float(df_m5["close"].iloc[-1])
    return buy_cond, sell_cond, current_price


# ---------------------------------------------------------------------------
# Telegram
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


def build_message(direction: str, symbol_name: str, cond: dict, price: float) -> str:
    tick = lambda v: "✅" if v else "❌"

    if direction == "BUY":
        header = f"🟢 <b>BUY (LONG) SIGNAL — {symbol_name}</b>"
    else:
        header = f"🔴 <b>SELL (SHORT) SIGNAL — {symbol_name}</b>"

    price_str = f"{price:,.2f}" if price > 10 else f"{price:.6f}"

    lines = [
        header,
        f"<b>Price:</b> {price_str}",
        f"<b>Time (UTC):</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
        "",
        "<b>Conditions:</b>",
    ] + [f"  {tick(v)} {k}" for k, v in cond.items()]
    return "\n".join(lines)


def maybe_send(symbol_name: str, direction: str, cond: dict, price: float) -> None:
    """Send alert if all conditions met and cooldown has passed."""
    if not all(cond.values()):
        return
    key = f"{symbol_name}_{direction}"
    now = datetime.now(timezone.utc)
    last = _last_signal.get(key)
    if last and (now - last).total_seconds() < SIGNAL_COOLDOWN_SECONDS:
        log.info("%s %s signal suppressed (cooldown).", symbol_name, direction)
        return
    msg = build_message(direction, symbol_name, cond, price)
    send_telegram(msg)
    _last_signal[key] = now
    log.info("%s %s signal sent.", symbol_name, direction)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    log.info("Bot started. Checking every %d seconds.", CHECK_INTERVAL)
    send_telegram(
        "🤖 <b>Signal bot online.</b>\n"
        "Watching: " + ", ".join(SYMBOLS.keys()) + "\n"
        "Scanning for: 🟢 BUY + 🔴 SELL signals"
    )

    while True:
        for name, ticker in SYMBOLS.items():
            try:
                log.info("Checking %s (%s)…", name, ticker)
                buy_cond, sell_cond, price = run_checks(ticker)

                log.info("%s BUY  conditions: %s", name, buy_cond)
                log.info("%s SELL conditions: %s", name, sell_cond)

                maybe_send(name, "BUY",  buy_cond,  price)
                maybe_send(name, "SELL", sell_cond, price)

            except Exception as exc:
                log.error("Error checking %s: %s", name, exc)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
