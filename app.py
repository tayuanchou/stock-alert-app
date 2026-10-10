"""Stock drop WhatsApp alerter (V1).

Checks each configured stock against yesterday's close and sends a WhatsApp
message through Twilio when the drop meets or exceeds the configured dollar
threshold. At most one alert per stock per trading day.

Run manually:  python app.py
"""

import json
import os
from datetime import datetime, time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
import yfinance as yf
from dotenv import load_dotenv
from twilio.rest import Client

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
STATE_PATH = BASE_DIR / "alert_state.json"

load_dotenv(BASE_DIR / ".env")


@lru_cache(maxsize=1)
def load_config():
    """Read config.yaml and return it as a dict. Cached per process."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state():
    """Read alert_state.json. Returns {} if it is missing or unreadable."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: could not read {STATE_PATH.name} ({exc}); starting empty")
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state):
    """Write the state dict to alert_state.json."""
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def market_timezone(config):
    """ZoneInfo for the configured market timezone."""
    return ZoneInfo(config.get("timezone", "America/New_York"))


def today_str(config):
    """Today's date in market timezone as YYYY-MM-DD."""
    return datetime.now(market_timezone(config)).strftime("%Y-%m-%d")


def is_market_open(config):
    """True when the current market-timezone time is within regular hours.

    Weekends are excluded. U.S. market holidays are ignored in V1.
    """
    hours = config.get("market_hours", {})
    start = time.fromisoformat(hours.get("start", "09:30"))
    end = time.fromisoformat(hours.get("end", "16:00"))

    now = datetime.now(market_timezone(config))
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    return start <= now.time() <= end


def get_price_info(symbol):
    """Return (current_price, previous_close) for a symbol.

    Previous close is taken from the most recent completed daily bar, so it
    stays correct while today's bar is still forming.
    """
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="7d", interval="1d")
    if history.empty:
        raise ValueError("No price data found")

    closes = history["Close"].dropna()
    if closes.empty:
        raise ValueError("No closing prices found")

    # Drop today's in-progress bar, if the feed already returned one.
    exchange_tz = history.index.tz or ZoneInfo("America/New_York")
    today = datetime.now(exchange_tz).date()
    completed = closes[[ts.date() != today for ts in closes.index]]
    if completed.empty:
        raise ValueError("No previous close available")
    previous_close = float(completed.iloc[-1])

    current_price = _latest_price(ticker)
    if current_price is None:
        current_price = float(closes.iloc[-1])

    return current_price, previous_close


def _latest_price(ticker):
    """Best-effort intraday price, or None if unavailable."""
    try:
        price = ticker.fast_info["last_price"]
    except Exception:
        return None
    return float(price) if price else None


def should_send_alert(symbol, state):
    """True when no alert has been sent for this symbol today."""
    return state.get(symbol) != today_str(load_config())


def mark_alert_sent(symbol, state):
    """Record today's date for the symbol in the state dict."""
    state[symbol] = today_str(load_config())


def format_alert(symbol, current_price, previous_close, drop, threshold):
    """Build the WhatsApp message body."""
    return (
        f"Stock Alert: {symbol} dropped ${abs(drop):.2f} today.\n\n"
        f"Previous close: ${previous_close:.2f}\n"
        f"Current price: ${current_price:.2f}\n"
        f"Threshold: ${float(threshold):.2f}\n\n"
        "Check the market before making any decision."
    )


def send_whatsapp_message(message):
    """Send a WhatsApp message through Twilio."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_WHATSAPP_FROM")
    to_number = os.getenv("MY_WHATSAPP_TO")

    missing = [
        name
        for name, value in (
            ("TWILIO_ACCOUNT_SID", account_sid),
            ("TWILIO_AUTH_TOKEN", auth_token),
            ("TWILIO_WHATSAPP_FROM", from_number),
            ("MY_WHATSAPP_TO", to_number),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    client = Client(account_sid, auth_token)
    sent = client.messages.create(body=message, from_=from_number, to=to_number)
    return sent.sid


def check_stock(stock, state):
    """Check one stock and alert if it dropped past its threshold.

    Returns True if an alert was sent.
    """
    symbol = stock["symbol"]
    threshold = float(stock["dollar_drop_threshold"])

    print(f"Checking {symbol}...")
    current_price, previous_close = get_price_info(symbol)
    drop = current_price - previous_close

    print(f"Previous close: {previous_close:.2f}")
    print(f"Current price: {current_price:.2f}")
    print(f"Drop: {drop:.2f}")

    if drop > -threshold:
        print("No alert needed")
        return False

    if not should_send_alert(symbol, state):
        print(f"Alert already sent today for {symbol}")
        return False

    send_whatsapp_message(
        format_alert(symbol, current_price, previous_close, drop, threshold)
    )
    mark_alert_sent(symbol, state)
    print(f"Alert sent for {symbol}")
    return True


def main():
    config = load_config()
    state = load_state()

    if not is_market_open(config):
        print("Market is closed. Exiting.")
        return

    stocks = config.get("stocks") or []
    if not stocks:
        print("No stocks configured. Exiting.")
        return

    for stock in stocks:
        try:
            check_stock(stock, state)
        except Exception as exc:
            print(f"Error checking {stock.get('symbol', '<unknown>')}: {exc}")
        print()

    save_state(state)


if __name__ == "__main__":
    main()
