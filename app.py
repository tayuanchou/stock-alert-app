"""Stock drop Telegram alerter (V1).

Checks each configured stock against yesterday's close and sends a Telegram
message when the drop meets or exceeds the configured dollar threshold.
At most one alert per stock per trading day.

Run manually:  python app.py
"""

import json
import os
from datetime import datetime, time
from functools import lru_cache
from pathlib import Path
from time import sleep
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yaml
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
STATE_PATH = BASE_DIR / "alert_state.json"

# This connection has been observed taking 20s+ for a Telegram round trip, so
# the timeout is generous and transient network errors are retried.
TELEGRAM_TIMEOUT_SECONDS = 30
TELEGRAM_ATTEMPTS = 3

load_dotenv(BASE_DIR / ".env")


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Read config.yaml and return it as a dict. Cached per process."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_state() -> dict[str, str]:
    """Read alert_state.json. Returns {} if it is missing or unreadable."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"Warning: could not read {STATE_PATH.name} ({exc}); starting empty")
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state: dict[str, str]) -> None:
    """Write the state dict to alert_state.json."""
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def market_timezone(config: dict[str, Any]) -> ZoneInfo:
    """ZoneInfo for the configured market timezone."""
    return ZoneInfo(config.get("timezone", "America/New_York"))


def today_str(config: dict[str, Any]) -> str:
    """Today's date in market timezone as YYYY-MM-DD."""
    return datetime.now(market_timezone(config)).strftime("%Y-%m-%d")


def is_market_open(config: dict[str, Any]) -> bool:
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


def get_price_info(symbol: str) -> tuple[float, float]:
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


def _latest_price(ticker: yf.Ticker) -> float | None:
    """Best-effort intraday price, or None if unavailable."""
    try:
        price = ticker.fast_info["last_price"]
    except Exception:
        return None
    return float(price) if price else None


def should_send_alert(symbol: str, state: dict[str, str]) -> bool:
    """True when no alert has been sent for this symbol today."""
    return state.get(symbol) != today_str(load_config())


def mark_alert_sent(symbol: str, state: dict[str, str]) -> None:
    """Record today's date for the symbol in the state dict."""
    state[symbol] = today_str(load_config())


def format_alert(
    symbol: str,
    current_price: float,
    previous_close: float,
    drop: float,
    threshold: float,
) -> str:
    """Build the alert message body."""
    return (
        f"Stock Alert: {symbol} dropped ${abs(drop):.2f} today.\n\n"
        f"Previous close: ${previous_close:.2f}\n"
        f"Current price: ${current_price:.2f}\n"
        f"Threshold: ${threshold:.2f}\n\n"
        "Check the market before making any decision."
    )


def send_telegram_message(message: str) -> int:
    """Send a message through the Telegram Bot API. Returns the message_id.

    Errors never include the request URL, because the bot token is embedded in
    it and callers print exceptions to the log.
    """
    required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    env: dict[str, str] = {}
    missing: list[str] = []
    for name in required:
        value = os.getenv(name)
        if value:
            env[name] = value
        else:
            missing.append(name)
    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}")

    url = f"https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/sendMessage"
    payload_out = {"chat_id": env["TELEGRAM_CHAT_ID"], "text": message}

    last_error = ""
    for attempt in range(1, TELEGRAM_ATTEMPTS + 1):
        try:
            response = requests.post(
                url, json=payload_out, timeout=TELEGRAM_TIMEOUT_SECONDS
            )
            break
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            if attempt < TELEGRAM_ATTEMPTS:
                print(
                    f"Telegram {last_error}, retrying ({attempt}/{TELEGRAM_ATTEMPTS})")
                sleep(2 * attempt)
    else:
        raise RuntimeError(
            f"Telegram request failed after {TELEGRAM_ATTEMPTS} attempts: {last_error}"
        ) from None

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(
            f"Telegram returned a non-JSON response (HTTP {response.status_code})"
        ) from None

    if not payload.get("ok"):
        raise RuntimeError(
            f"Telegram API error {payload.get('error_code')}: "
            f"{payload.get('description')}"
        )
    return int(payload["result"]["message_id"])


def check_stock(stock: dict[str, Any], state: dict[str, str]) -> bool:
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

    print(
        f"QUALIFIED: {symbol} dropped ${abs(drop):.2f}, "
        f"at or beyond its ${threshold:.2f} threshold"
    )

    if not should_send_alert(symbol, state):
        print(f"Alert already sent today for {symbol}")
        return False

    message = format_alert(symbol, current_price,
                           previous_close, drop, threshold)
    print("Message body:")
    print(message)
    send_telegram_message(message)
    mark_alert_sent(symbol, state)
    print(f"Alert sent for {symbol}")
    return True


def main() -> None:
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
