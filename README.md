# Stock Drop Telegram Alerts (V1)

Sends you a Telegram message when a configured stock drops more than a set dollar
amount from the previous trading day's close. At most one alert per stock per day.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Copy the environment template and fill in your Telegram credentials:

```bash
copy .env.example .env         # Windows
# cp .env.example .env         # macOS / Linux
```

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHAT_ID=your_numeric_chat_id
```

### Creating the Telegram bot

1. In Telegram, open [@BotFather](https://t.me/botfather) and send `/newbot`.
2. Give it a display name (anything, e.g. `Peter Stock Alerts`).
3. Give it a username — 5–32 characters, letters/digits/underscores only, and it
   **must end in `bot`** (e.g. `peter_stock_alert_bot`). This cannot be changed
   later.
4. BotFather replies with a token shaped like
   `110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`. That is
   `TELEGRAM_BOT_TOKEN`. Treat it as a password — anyone holding it controls the
   bot. `/token` reissues it if it leaks.
5. Open `https://t.me/<your_bot_username>` and tap **Start**. This is required:
   a bot cannot message anyone who has not first started a conversation with it.
6. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read
   the integer at `result[0].message.chat.id`. That is `TELEGRAM_CHAT_ID`. If
   `result` is empty, step 5 did not register — send the bot any message and
   reload.

Unlike WhatsApp, Telegram has no session window, no message templates, and no
approval process, so a scheduled job can send at any time without preconditions.

## Configure the watchlist

`config.yaml`:

```yaml
timezone: America/New_York

market_hours:
  start: "09:30"
  end: "16:00"

stocks:
  - symbol: NVDA
    dollar_drop_threshold: 10

  - symbol: MU
    dollar_drop_threshold: 10

  - symbol: AVGO
    dollar_drop_threshold: 10
```

Each stock gets its own threshold. An alert fires when
`current_price - previous_close <= -dollar_drop_threshold`.

## Run

```bash
python app.py
```

Output looks like:

```text
Checking NVDA...
Previous close: 185.40
Current price: 174.15
Drop: -11.25
Alert sent for NVDA

Checking MU...
Previous close: 142.10
Current price: 138.75
Drop: -3.35
No alert needed
```

The script exits immediately with `Market is closed. Exiting.` outside
Mon–Fri 09:30–16:00 Eastern, so it is safe to schedule it around the clock.
A failure on one symbol is logged and the remaining symbols are still checked.

## Tests

```bash
python -m unittest -v
```

Covers all five example cases from the spec plus market-hours boundaries, state
round-tripping, message formatting, and the Telegram transport (including that a
network failure never leaks the bot token into the log). No network calls —
price fetching and `requests.post` are both stubbed.

## Scheduling every 15 minutes

### Windows Task Scheduler

```powershell
schtasks /create /tn "StockDropAlerts" /sc minute /mo 15 ^
  /tr "C:\Users\peter\whatsapp_alerting\venv\Scripts\python.exe C:\Users\peter\whatsapp_alerting\app.py"
```

Set the task's **Start in** directory to the project folder so `config.yaml`,
`.env`, and `alert_state.json` resolve. (`app.py` resolves these relative to its
own location, so this is belt-and-braces.)

### cron (macOS / Linux)

```cron
*/15 9-16 * * 1-5 cd /path/to/whatsapp_alerting && venv/bin/python app.py >> alerts.log 2>&1
```

## State

`alert_state.json` records the last date an alert was sent per symbol:

```json
{
  "NVDA": "2026-10-05"
}
```

Delete the file (or an entry) to allow a repeat alert the same day. It is
gitignored because it is per-machine runtime state, not configuration.

## V1 limitations

- U.S. market holidays are not handled — on a holiday the script will run, find
  no new bar, and simply report no drop.
- `yfinance` is an unofficial feed, quotes may lag by up to ~15 minutes.
- Dollar drops only; percentage thresholds are a V2 item.
- The bot can only message a chat that has pressed **Start** on it.
