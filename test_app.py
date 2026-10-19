"""Tests for the spec's example test cases (section 18).

Run:  python -m unittest -v
"""

import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import app


class CheckStockTests(unittest.TestCase):
    """Each case patches price fetching and Telegram so no network is touched."""

    def setUp(self):
        self.sent = []
        patcher = patch.object(app, "send_telegram_message", self.sent.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.today = app.today_str(app.load_config())

    def check(self, previous_close, current_price, threshold=10, state=None):
        state = {} if state is None else state
        stock = {"symbol": "NVDA", "dollar_drop_threshold": threshold}
        with patch.object(
            app, "get_price_info", return_value=(current_price, previous_close)
        ):
            sent = app.check_stock(stock, state)
        return sent, state

    def test_1_drop_exceeds_threshold_sends_alert(self):
        sent, state = self.check(previous_close=185, current_price=174)
        self.assertTrue(sent)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("NVDA dropped $11.00", self.sent[0])
        self.assertEqual(state["NVDA"], self.today)

    def test_2_drop_under_threshold_no_alert(self):
        sent, state = self.check(previous_close=185, current_price=180)
        self.assertFalse(sent)
        self.assertEqual(self.sent, [])
        self.assertEqual(state, {})

    def test_3_stock_rises_no_alert(self):
        sent, _ = self.check(previous_close=185, current_price=190)
        self.assertFalse(sent)
        self.assertEqual(self.sent, [])

    def test_4_alert_already_sent_today(self):
        sent, _ = self.check(
            previous_close=185, current_price=174, state={"NVDA": self.today}
        )
        self.assertFalse(sent)
        self.assertEqual(self.sent, [])

    def test_4b_alert_sent_yesterday_still_alerts(self):
        sent, state = self.check(
            previous_close=185, current_price=174, state={"NVDA": "2000-01-01"}
        )
        self.assertTrue(sent)
        self.assertEqual(state["NVDA"], self.today)

    def test_drop_exactly_at_threshold_sends_alert(self):
        # Spec section 17.5: alert when the drop is >= the threshold.
        sent, _ = self.check(previous_close=185, current_price=175)
        self.assertTrue(sent)


class MainTests(unittest.TestCase):
    def test_5_one_failing_stock_does_not_stop_the_run(self):
        checked = []

        def fake_get_price_info(symbol):
            checked.append(symbol)
            if symbol == "MU":
                raise ValueError("No price data found")
            return 174.0, 185.0

        config = {
            "timezone": "America/New_York",
            "market_hours": {"start": "09:30", "end": "16:00"},
            "stocks": [
                {"symbol": "NVDA", "dollar_drop_threshold": 10},
                {"symbol": "MU", "dollar_drop_threshold": 10},
                {"symbol": "AVGO", "dollar_drop_threshold": 10},
            ],
        }
        saved = {}

        with patch.object(app, "load_config", return_value=config), patch.object(
            app, "is_market_open", return_value=True
        ), patch.object(app, "load_state", return_value={}), patch.object(
            app, "save_state", saved.update
        ), patch.object(
            app, "get_price_info", fake_get_price_info
        ), patch.object(
            app, "send_telegram_message", lambda _message: None
        ):
            app.main()

        self.assertEqual(checked, ["NVDA", "MU", "AVGO"])
        self.assertEqual(sorted(saved), ["AVGO", "NVDA"])

    def test_market_closed_exits_without_checking(self):
        with patch.object(app, "is_market_open", return_value=False), patch.object(
            app, "get_price_info", side_effect=AssertionError("should not be called")
        ):
            app.main()


class MarketHoursTests(unittest.TestCase):
    CONFIG = {
        "timezone": "America/New_York",
        "market_hours": {"start": "09:30", "end": "16:00"},
    }

    def assert_open(self, iso, expected):
        moment = datetime.fromisoformat(iso).replace(
            tzinfo=app.market_timezone(self.CONFIG)
        )

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003 - signature must match datetime.now
                return moment

        with patch.object(app, "datetime", FrozenDatetime):
            self.assertEqual(app.is_market_open(self.CONFIG), expected, iso)

    def test_hours(self):
        self.assert_open("2026-10-05T09:29:00", False)  # Monday, pre-open
        self.assert_open("2026-10-05T09:30:00", True)  # Monday, open bell
        self.assert_open("2026-10-05T12:00:00", True)  # Monday, midday
        self.assert_open("2026-10-05T16:00:00", True)  # Monday, close bell
        self.assert_open("2026-10-05T16:01:00", False)  # Monday, after close
        self.assert_open("2026-10-03T12:00:00", False)  # Saturday
        self.assert_open("2026-10-04T12:00:00", False)  # Sunday


class StateTests(unittest.TestCase):
    def test_load_state_missing_file_returns_empty_dict(self):
        with patch.object(app, "STATE_PATH", app.BASE_DIR / "does_not_exist.json"):
            self.assertEqual(app.load_state(), {})

    def test_save_then_load_round_trips(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = app.Path(tmp) / "alert_state.json"
            with patch.object(app, "STATE_PATH", path):
                app.save_state({"NVDA": "2026-10-05"})
                self.assertEqual(app.load_state(), {"NVDA": "2026-10-05"})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), {"NVDA": "2026-10-05"}
            )

    def test_load_state_corrupt_file_returns_empty_dict(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = app.Path(tmp) / "alert_state.json"
            path.write_text("{not json", encoding="utf-8")
            with patch.object(app, "STATE_PATH", path):
                self.assertEqual(app.load_state(), {})


class MessageFormatTests(unittest.TestCase):
    def test_matches_spec_example(self):
        message = app.format_alert("NVDA", 174.15, 185.40, 174.15 - 185.40, 10)
        self.assertEqual(
            message,
            "Stock Alert: NVDA dropped $11.25 today.\n\n"
            "Previous close: $185.40\n"
            "Current price: $174.15\n"
            "Threshold: $10.00\n\n"
            "Check the market before making any decision.",
        )


class SendTelegramTests(unittest.TestCase):
    """Transport tests. requests.post is always stubbed, so nothing is sent."""

    CREDS = {"TELEGRAM_BOT_TOKEN": "123:SECRET", "TELEGRAM_CHAT_ID": "456"}

    def test_missing_credentials_raise_a_clear_error(self):
        with patch.dict(
            app.os.environ,
            {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""},
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                app.send_telegram_message("hello")
        self.assertIn("TELEGRAM_BOT_TOKEN", str(ctx.exception))

    def test_posts_to_sendmessage_and_returns_message_id(self):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"ok": True, "result": {"message_id": 42}},
            )

        with patch.dict(app.os.environ, self.CREDS, clear=False), patch.object(
            app.requests, "post", fake_post
        ):
            message_id = app.send_telegram_message("hello")

        self.assertEqual(message_id, 42)
        self.assertEqual(
            captured["url"], "https://api.telegram.org/bot123:SECRET/sendMessage"
        )
        self.assertEqual(captured["json"], {"chat_id": "456", "text": "hello"})
        self.assertEqual(captured["timeout"], app.TELEGRAM_TIMEOUT_SECONDS)

    def test_api_rejection_raises_with_the_description(self):
        def fake_post(url, json=None, timeout=None):
            return SimpleNamespace(
                status_code=400,
                json=lambda: {
                    "ok": False,
                    "error_code": 400,
                    "description": "chat not found",
                },
            )

        with patch.dict(app.os.environ, self.CREDS, clear=False), patch.object(
            app.requests, "post", fake_post
        ):
            with self.assertRaises(RuntimeError) as ctx:
                app.send_telegram_message("hello")
        self.assertIn("chat not found", str(ctx.exception))

    def test_network_failure_does_not_leak_the_token(self):
        def fake_post(url, json=None, timeout=None):
            # requests puts the full URL - token included - in its own messages.
            raise app.requests.ConnectionError(f"failed connecting to {url}")

        with patch.dict(app.os.environ, self.CREDS, clear=False), patch.object(
            app.requests, "post", fake_post
        ), patch.object(app, "sleep", lambda _s: None):
            with self.assertRaises(RuntimeError) as ctx:
                app.send_telegram_message("hello")
        text = str(ctx.exception)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("api.telegram.org", text)
        self.assertIn("ConnectionError", text)

    def test_transient_failure_is_retried_then_succeeds(self):
        calls = []

        def fake_post(url, json=None, timeout=None):
            calls.append(timeout)
            if len(calls) < 3:
                raise app.requests.ReadTimeout("timed out")
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"ok": True, "result": {"message_id": 7}},
            )

        with patch.dict(app.os.environ, self.CREDS, clear=False), patch.object(
            app.requests, "post", fake_post
        ), patch.object(app, "sleep", lambda _s: None):
            self.assertEqual(app.send_telegram_message("hello"), 7)

        self.assertEqual(len(calls), 3)
        self.assertEqual(calls, [app.TELEGRAM_TIMEOUT_SECONDS] * 3)

    def test_api_rejection_is_not_retried(self):
        calls = []

        def fake_post(url, json=None, timeout=None):
            calls.append(1)
            return SimpleNamespace(
                status_code=400,
                json=lambda: {"ok": False, "error_code": 400, "description": "nope"},
            )

        with patch.dict(app.os.environ, self.CREDS, clear=False), patch.object(
            app.requests, "post", fake_post
        ), patch.object(app, "sleep", lambda _s: None):
            with self.assertRaises(RuntimeError):
                app.send_telegram_message("hello")

        self.assertEqual(len(calls), 1, "a deterministic rejection must not retry")


if __name__ == "__main__":
    unittest.main()
