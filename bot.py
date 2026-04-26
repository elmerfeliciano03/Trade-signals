"""
Telegram Signal Bot - Yahoo Finance Version
Uses latest yfinance with curl_cffi for better rate limit handling
"""

import os
import logging
import requests
from datetime import datetime, timezone
import time
import json

# Try to import curl_cffi for better rate limit handling
try:
    from curl_cffi import requests as curl_requests
    USE_CURL_CFFI = True
    logging.info("Using curl_cffi for better rate limit handling")
except ImportError:
    import requests as curl_requests
    USE_CURL_CFFI = False
    logging.info("curl_cffi not available, using standard requests")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============ CONFIGURATION ============
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

COOLDOWN_FILE = "/tmp/last_signal.txt"
CACHE_FILE = "/tmp/price_cache.json"
SIGNAL_COOLDOWN = 3600  # 1 hour between signals
CACHE_DURATION = 240  # Cache prices for 4 minutes

# Yahoo Finance symbols (updated working symbols)
ASSETS = {
    "XAUUSD (Gold)": {
        "type": "forex",
        "symbol": "XAUUSD=X",
        "fallback": "GC=F",
        "emoji": "🥇"
    },
    "Crude Oil WTI": {
        "type": "commodity", 
        "symbol": "CL=F",
        "fallback": "BZ=F",
        "emoji": "🛢️"
    },
    "Bitcoin": {
        "type": "crypto",
        "symbol": "BTC-USD",
        "emoji": "₿"
    },
    "Ethereum": {
        "type": "crypto",
        "symbol": "ETH-USD",
        "emoji": "Ξ"
    }
}

# Create a session with browser impersonation (bypasses rate limits)
def create_session():
    """Create a session that mimics a real browser"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Origin': 'https://finance.yahoo.com',
        'Referer': 'https://finance.yahoo.com/'
    }
    
    if USE_CURL_CFFI:
        try:
            session = curl_requests.Session(impersonate="chrome120")
            session.headers.update(headers)
            return session
        except:
            pass
    
    session = requests.Session()
    session.headers.update(headers)
    return session

# Global session
YF_SESSION = create_session()

# Import yfinance with custom session
import yfinance as yf
yf.set_config(proxy=None)

# ============ CACHE FUNCTIONS ============
def get_cached_price(asset_name):
    """Get price from cache if still valid"""
    try:
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            timestamp = cache.get('timestamp', 0)
            if (datetime.now(timezone.utc).timestamp() - timestamp) < CACHE_DURATION:
                return cache.get(asset_name)
    except:
        pass
    return None

def save_to_cache(asset_name, price):
    """Save price to cache"""
    try:
        cache = {}
        try:
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
        except:
            pass
        
        cache[asset_name] = price
        cache['timestamp'] = datetime.now(timezone.utc).timestamp()
        
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except:
        pass

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

# ============ YAHOO FINANCE DATA (with fallback and retry) ============
def fetch_yahoo_data(symbol, retry_count=3):
    """Fetch current price from Yahoo Finance with retry and fallback"""
    
    for attempt in range(retry_count):
        try:
            ticker = yf.Ticker(symbol, session=YF_SESSION)
            
            # Try to get current price via fast_info first (faster, less likely to rate limit)
            try:
                fast_info = ticker.fast_info
                if hasattr(fast_info, 'last_price') and fast_info.last_price:
                    price = float(fast_info.last_price)
                    if price > 0:
                        return price
            except:
                pass
            
            # Fallback to history
            hist = ticker.history(period="1d", interval="1m", timeout=15)
            
            if not hist.empty:
                price = float(hist['Close'].iloc[-1])
                if price > 0:
                    return price
            
            # Try history with longer period
            hist = ticker.history(period="5d", interval="5m", timeout=15)
            if not hist.empty:
                price = float(hist['Close'].iloc[-1])
                if price > 0:
                    return price
            
        except Exception as e:
            error_msg = str(e)
            if "Rate limited" in error_msg or "Too Many Requests" in error_msg:
                log.warning(f"Rate limit hit for {symbol}, waiting...")
                time.sleep(3)
            else:
                log.debug(f"Attempt {attempt+1} failed for {symbol}: {e}")
        
        if attempt < retry_count - 1:
            time.sleep(2)
    
    return 0

def get_asset_price(asset_name, asset_config):
    """Get price for an asset with caching and fallback"""
    
    # Check cache first
    cached = get_cached_price(asset_name)
    if cached:
        log.debug(f"Using cached price for {asset_name}: ${cached:,.2f}")
        return cached
    
    # Try primary symbol
    symbol = asset_config["symbol"]
    price = fetch_yahoo_data(symbol)
    
    # If primary fails and fallback exists, try fallback
    if price == 0 and "fallback" in asset_config:
        fallback = asset_config["fallback"]
        log.info(f"Primary symbol {symbol} failed, trying fallback {fallback}")
        price = fetch_yahoo_data(fallback)
    
    if price > 0:
        save_to_cache(asset_name, price)
        return price
    
    return 0

def get_historical_data(symbol, period="1mo", interval="1d"):
    """Get historical data for indicators"""
    try:
        ticker = yf.Ticker(symbol, session=YF_SESSION)
        hist = ticker.history(period=period, interval=interval, timeout=15)
        
        if not hist.empty:
            return hist['Close'].values.tolist()
    except Exception as e:
        log.debug(f"Historical data error for {symbol}: {e}")
    
    return []

# ============ TECHNICAL INDICATORS ============
def calculate_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calculate_ema(prices, period):
    """Exponential Moving Average"""
    if len(prices) < period:
        return None
    
    multiplier = 2 / (period + 1)
    ema = prices[-period]
    
    for price in prices[-period+1:]:
        ema = (price - ema) * multiplier + ema
    
    return ema

def generate_signal(name, asset_config, current_price):
    """Generate BUY signal based on technical indicators"""
    
    symbol = asset_config.get("symbol")
    if not symbol:
        return None, None
    
    # Get historical data
    historical = get_historical_data(symbol, period="2mo", interval="1d")
    
    if not historical or len(historical) < 30 or current_price == 0:
        return None, None
    
    # Calculate indicators
    sma_20 = calculate_sma(historical, 20)
    sma_50 = calculate_sma(historical, 50) if len(historical) >= 50 else None
    ema_20 = calculate_ema(historical, 20)
    
    if sma_20 is None:
        return None, None
    
    reasons = []
    
    # Buy signal: Price above SMA20 (momentum)
    if current_price > sma_20:
        reasons.append(f"Price (${current_price:,.2f}) > SMA20 (${sma_20:,.2f})")
    
    # Stronger signal: Golden cross setup
    if sma_50 and current_price > sma_20 and current_price > sma_50:
        reasons.append(f"Price above both SMA20 and SMA50 (${sma_50:,.2f})")
    
    # EMA confirmation
    if ema_20 and current_price > ema_20:
        reasons.append(f"Price above EMA20 (${ema_20:,.2f})")
    
    if reasons:
        return "BUY", " | ".join(reasons[:2])
    
    return None, None

# ============ TELEGRAM ============
def send_signal(name, signal_type, reason, price, emoji):
    """Send trading signal to Telegram"""
    
    message = f"""<b>🚀 {signal_type} SIGNAL — {emoji} {name}</b>

<b>Price:</b> ${price:,.2f}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC

<b>Signal Details:</b>
{reason}

<code>⚠️ Always use stop losses. Trade at your own risk.</code>"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        r.raise_for_status()
        log.info(f"✅ Signal sent for {name}")
        return True
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

# ============ MAIN ============
def main():
    log.info("=" * 50)
    log.info("📊 SIGNAL BOT - YAHOO FINANCE VERSION")
    log.info(f"curl_cffi available: {USE_CURL_CFFI}")
    log.info(f"Monitoring {len(ASSETS)} assets")
    log.info("=" * 50)
    
    signals_found = 0
    
    for name, asset_config in ASSETS.items():
        log.info(f"🔍 Checking {name}...")
        
        # Get current price (cached)
        current_price = get_asset_price(name, asset_config)
        
        if current_price > 0:
            log.info(f"💰 {name}: ${current_price:,.2f}")
            
            # Generate signal
            signal_type, reason = generate_signal(name, asset_config, current_price)
            
            if signal_type and reason:
                if not check_cooldown(name):
                    if send_signal(name, signal_type, reason, current_price, asset_config["emoji"]):
                        save_cooldown(name)
                        signals_found += 1
                        log.info(f"🔔 {signal_type} signal sent for {name}")
                else:
                    log.info(f"⏰ {name} - signal blocked (cooldown)")
            else:
                log.info(f"📊 {name} - no signal at this time")
        else:
            log.warning(f"⚠️ {name} - price unavailable (retrying next cycle)")
        
        # Delay between assets to avoid rate limits
        time.sleep(3)
    
    if signals_found > 0:
        log.info(f"✅ Sent {signals_found} signal(s) this cycle")
    else:
        log.info("✅ No signals this cycle")
    
    log.info("=" * 50)

if __name__ == "__main__":
    main()
