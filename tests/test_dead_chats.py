"""A chat Telegram cannot reach (blocked, or 'chat not found') is dropped, not retried forever."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
from alerts import is_dead_chat, send_once  # noqa: E402
from unittest import mock  # noqa: E402


class Err(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


class Bot:
    def __init__(self, failures):
        self.failures, self.sent = failures, []

    def send(self, message, chat_id=None, reply_markup=None):
        if chat_id in self.failures:
            raise self.failures[chat_id]
        self.sent.append(chat_id)


class DeadChats(unittest.TestCase):
    def test_which_errors_count(self):
        self.assertTrue(is_dead_chat(Err(403, "Forbidden: bot was blocked by the user")))
        self.assertTrue(is_dead_chat(Err(400, "Telegram error 400: Bad Request: chat not found")))
        self.assertFalse(is_dead_chat(Err(400, "Bad Request: message is too long")))
        self.assertFalse(is_dead_chat(Err(429, "Too Many Requests")))
        self.assertFalse(is_dead_chat(RuntimeError("boom")))

    def run_send(self, failures):
        bot, dropped, state = Bot(failures), [], {}
        with mock.patch.object(alerts, "save_state"), mock.patch.object(alerts.time, "sleep"):
            send_once(bot, state, "k|x|2026-11-12T07:00:00+00:00", "1h", "hi",
                      recipients=["a", "b", "c"], on_blocked=dropped.append)
        return bot, dropped, state

    def test_chat_not_found_is_dropped_and_others_still_get_it(self):
        bot, dropped, state = self.run_send({"b": Err(400, "Bad Request: chat not found")})
        self.assertEqual(bot.sent, ["a", "c"])
        self.assertEqual(dropped, ["b"])
        self.assertTrue(list(state.values())[0]["1h"])

    def test_blocked_is_still_dropped_and_other_errors_are_not(self):
        _, dropped, _ = self.run_send({"a": Err(403, "blocked"), "c": Err(400, "message is too long")})
        self.assertEqual(dropped, ["a"])


if __name__ == "__main__":
    unittest.main()
