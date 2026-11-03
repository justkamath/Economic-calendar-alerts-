"""Run from the repo root:  python -m unittest discover -s tests -v"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notifiers.telegram import TelegramError, TelegramNotifier, parse_chat_ids  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(parse_chat_ids("123"), ["123"])

    def test_many_with_spaces_groups_and_duplicates(self):
        self.assertEqual(parse_chat_ids(" 111, -100222 ,111;333\n444"),
                         ["111", "-100222", "333", "444"])

    def test_empty(self):
        self.assertEqual(parse_chat_ids(None), [])
        self.assertEqual(parse_chat_ids(" , "), [])


class SendTests(unittest.TestCase):
    def notifier(self, ids="1,-100,3"):
        return TelegramNotifier(token="T", chat_id=ids)

    def run_send(self, notifier, side_effect, **kw):
        out = io.StringIO()
        with mock.patch.object(TelegramNotifier, "_call", side_effect=side_effect) as call, \
                contextlib.redirect_stdout(out):
            try:
                notifier.send("hi", **kw)
                error = None
            except TelegramError as e:
                error = e
        return call, out.getvalue(), error

    def test_validate(self):
        self.assertIsNone(self.notifier().validate())
        with self.assertRaises(RuntimeError):
            TelegramNotifier(token="T", chat_id=" , ").validate()
        with self.assertRaises(RuntimeError):
            TelegramNotifier(token="", chat_id="1").validate()

    def test_sends_to_every_chat(self):
        call, out, error = self.run_send(self.notifier(), lambda m, p: {"message_id": 7})
        self.assertIsNone(error)
        self.assertEqual([c.args[1]["chat_id"] for c in call.call_args_list], ["1", "-100", "3"])
        self.assertIn("Delivered to 3 of 3 chats", out)

    def test_one_blocked_chat_does_not_stop_the_rest(self):
        def fake(method, params):
            if params["chat_id"] == "-100":
                raise TelegramError(403, "bot was blocked")
            return {"message_id": 1}
        call, out, error = self.run_send(self.notifier(), fake)
        self.assertIsNone(error)
        self.assertEqual(call.call_count, 3)
        self.assertIn("Delivered to 2 of 3 chats", out)
        self.assertNotIn("-100", out)          # IDs never reach the logs

    def test_all_failing_raises_so_the_alert_is_retried(self):
        def fake(method, params):
            raise TelegramError(500, "down")
        _, _, error = self.run_send(self.notifier(), fake)
        self.assertEqual(error.code, 500)

    def test_single_chat_failure_still_raises(self):
        def fake(method, params):
            raise TelegramError(400, "bad")
        _, _, error = self.run_send(self.notifier("1"), fake)
        self.assertIsNotNone(error)

    def test_explicit_chat_id_overrides(self):
        call, _, _ = self.run_send(self.notifier(), lambda m, p: {"message_id": 1}, chat_id="9")
        self.assertEqual([c.args[1]["chat_id"] for c in call.call_args_list], ["9"])


class MenuCommandTests(unittest.TestCase):
    def test_set_commands_sends_the_list(self):
        notifier = TelegramNotifier(token="T", chat_id="1")
        with mock.patch.object(TelegramNotifier, "_call") as call:
            notifier.set_commands([("menu", "Open the buttons")])
        method, params = call.call_args.args
        self.assertEqual(method, "setMyCommands")
        self.assertIn('"command": "menu"', params["commands"])
        self.assertIn("Open the buttons", params["commands"])


if __name__ == "__main__":
    unittest.main()
