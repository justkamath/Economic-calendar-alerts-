"""Runs main.main() end to end with fakes, so wiring mistakes show up here.
Run from the repo root:  python -m unittest discover -s tests -v"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
import main  # noqa: E402

KEY = "a-long-random-secret-key-123456"


class FakeBot:
    def __init__(self, updates=()):
        self.chat_ids = ["100"]
        self.updates, self.sent = list(updates), []

    def validate(self):
        pass

    def get_updates(self, offset=0, limit=100):
        out, self.updates = [u for u in self.updates if u["update_id"] >= offset], []
        return out

    def send(self, message, chat_id=None, reply_markup=None):
        self.sent.append((chat_id, message))


class MainTests(unittest.TestCase):
    def run_main(self, bot, key=KEY, calendar=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "subscribers.enc")
        out = io.StringIO()
        with mock.patch.object(main, "TelegramNotifier", return_value=bot), \
                mock.patch.object(main, "fetch_calendar", return_value=list(calendar)), \
                mock.patch.object(main, "maybe_send_summary"), \
                mock.patch.object(main, "maybe_sync"), \
                mock.patch.object(main, "load_state", return_value={}), \
                mock.patch("alerts.save_state"), mock.patch("alerts.prune_state"), \
                mock.patch.object(cfg, "SUBSCRIBERS_KEY", key), \
                mock.patch.object(cfg, "SUBSCRIBERS_FILE", path), \
                contextlib.redirect_stdout(out):
            main.main()
        return out.getvalue(), path

    def test_commands_are_read_even_when_nobody_has_subscribed_yet(self):
        update = {"update_id": 1, "message": {"chat": {"id": 7, "type": "private"},
                                              "text": "/start"}}
        bot = FakeBot([update])
        out, path = self.run_main(bot)
        self.assertIn("Commands handled: 1 | subscribers: 1", out)
        self.assertEqual([c for c, _ in bot.sent], ["7"])
        self.assertTrue(os.path.exists(path))          # the new subscriber was saved

    def test_empty_run_still_reports_the_command_line(self):
        out, path = self.run_main(FakeBot())
        self.assertIn("Commands handled: 0 | subscribers: 0", out)
        self.assertFalse(os.path.exists(path))

    def test_without_a_key_it_is_owner_only(self):
        bot = FakeBot()
        out, _ = self.run_main(bot, key="")
        self.assertIn("Subscriptions off", out)
        self.assertNotIn("Commands handled", out)
        self.assertIn("Calendar check completed.", out)


if __name__ == "__main__":
    unittest.main()
