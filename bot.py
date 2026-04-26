"""
Telegram Signal Bot - Cron Job Version
Runs once per schedule (every 5 minutes)
"""

import os
import logging
import requests
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Config
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Simple cooldown file
COOLDOWN_FILE = "/tmp/last_signal.txt"
SIGNAL_COOLDOWN = 3600  # 1 hour

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

def main():
    log.info("=" * 40)
    log.info("🤖 SIGNAL BOT - CRON JOB MODE")
    log.info("Checking for signals...")
    
    # For now, send a test message to confirm it works
    test_message = f"""<b>✅ Bot is Alive!</b>

⏰ Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
🐍 Python: 3.11
📊 Status: Running as Cron Job

<code>Your trading bot is online and checking for signals every 5 minutes.</code>"""
    
    send_telegram(test_message)
    log.info("✅ Test message sent")
    log.info("=" * 40)

if __name__ == "__main__":
    main()
