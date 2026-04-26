# XAUUSD & Crude Oil WTI — Telegram Signal Bot

Sends a Telegram alert when all five BUY conditions are met on either instrument:

1. Price > EMA200 on H4  
2. Price > EMA200 on H1  
3. Price > EMA200 on M5  
4. EMA9 > EMA200 on M5  
5. MACD line crosses Signal from below, both lines below zero (M5)

Data source: Yahoo Finance (free, no API key needed)  
Tickers used: `GC=F` (Gold/XAUUSD), `CL=F` (WTI Crude Oil)

---

## 1. Create your Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **token** it gives you (looks like `123456:ABCdef...`)
4. Message **@userinfobot** to get your **chat ID**

---

## 2. Deploy to Railway (recommended — free tier available)

1. Push this folder to a GitHub repository
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo
4. Go to **Variables** and add:
   - `TELEGRAM_TOKEN` → your bot token
   - `TELEGRAM_CHAT_ID` → your chat ID
   - `CHECK_INTERVAL_SECONDS` → `300` (or adjust)
5. Railway detects the `Procfile` and runs `python bot.py` as a worker automatically

---

## 3. Deploy to Render (alternative)

1. Push to GitHub
2. Go to [render.com](https://render.com) → New → Background Worker
3. Connect your repo
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `python bot.py`
6. Add environment variables in the Render dashboard (same as above)

---

## 4. Run locally for testing

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your real token and chat ID
export $(cat .env | xargs)
python bot.py
```

---

## Signal message example

```
🟡 BUY SIGNAL — XAUUSD
Price: 2341.5200
Time (UTC): 2026-04-26 09:35

Conditions met:
  ✅ H4 price > EMA200
  ✅ H1 price > EMA200
  ✅ M5 price > EMA200
  ✅ M5 EMA9 > EMA200
  ✅ MACD bull cross <0
```

---

## Notes

- Yahoo Finance 5-minute data is only available for the last 5 days. The bot works within this window.
- The bot suppresses duplicate alerts for the same symbol for 1 hour after a signal fires (configurable via `SIGNAL_COOLDOWN_SECONDS` in `bot.py`).
- H4 bars are resampled from H1 data since Yahoo Finance does not offer a native 4h interval.
- Futures prices from Yahoo Finance may differ slightly from your broker's quotes (different sessions/roll dates) — use for signal awareness, not exact entry prices.
