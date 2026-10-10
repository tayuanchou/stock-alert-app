# Stock Drop WhatsApp Alerts (V1)

Sends you a WhatsApp message when a configured stock drops more than a set dollar
amount from the previous trading day's close. At most one alert per stock per day.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Copy the environment template and fill in your Twilio credentials:

```bash
copy .env.example .env         # Windows
# cp .env.example .env         # macOS / Linux
```

```bash
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
MY_WHATSAPP_TO=whatsapp:+1YOUR_PHONE_NUMBER
```

`TWILIO_WHATSAPP_FROM` is the Twilio WhatsApp sender (the number above is
Twilio's shared sandbox sender). `MY_WHATSAPP_TO` is your own WhatsApp number.

### Twilio WhatsApp sandbox

For personal use, the sandbox is enough. In the Twilio Console go to
**Messaging → Try it out → Send a WhatsApp message**, then send the join code it
shows you (e.g. `join <two-words>`) from your phone to the sandbox number. The
sandbox session expires after 72 hours of inactivity — re-send the join code to
renew it. To send outside a 24-hour conversation window you need an approved
template or a production WhatsApp sender.

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
round-tripping, and message formatting. No network calls — price fetching and
Twilio are both stubbed.

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
