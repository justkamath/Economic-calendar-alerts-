"""Run from the repo root:  python -m unittest discover -s tests -v"""
import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar_service as svc  # noqa: E402
import health  # noqa: E402
from sources import CalendarSource  # noqa: E402
from storage import open_storage  # noqa: E402

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)


def at(hours=0, minutes=0):
    return T0 + timedelta(hours=hours, minutes=minutes)


class Run:
    def __init__(self):
        self.db = open_storage("sqlite:///:memory:")
        self.sent = []

    def check(self, results, when):
        with contextlib.redirect_stdout(io.StringIO()):
            health.check(self.db, results, when, self.sent.append)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.r = Run()
        self.addCleanup(self.r.db.close)

    def test_working_source_is_remembered_and_never_nags(self):
        self.r.check({"forex_factory": 100}, T0)
        self.r.check({"forex_factory": 100}, at(1))
        self.assertEqual(self.r.db.last_success("forex_factory"), at(1))
        self.assertEqual(self.r.sent, [])

    def test_success_is_saved_at_most_every_30_minutes(self):
        self.r.check({"forex_factory": 1}, T0)
        self.r.check({"forex_factory": 1}, at(minutes=10))
        self.assertEqual(self.r.db.last_success("forex_factory"), T0)
        self.r.check({"forex_factory": 1}, at(minutes=31))
        self.assertEqual(self.r.db.last_success("forex_factory"), at(minutes=31))

    def test_short_outage_stays_quiet(self):
        self.r.check({"forex_factory": 1}, T0)
        self.r.check({"forex_factory": None}, at(2, 55))
        self.assertEqual(self.r.sent, [])

    def test_long_outage_warns_once_then_says_back(self):
        self.r.check({"forex_factory": 1}, T0)
        self.r.check({"forex_factory": None}, at(3))
        self.r.check({"forex_factory": None}, at(4))
        self.r.check({"forex_factory": None}, at(9))
        self.assertEqual(len(self.r.sent), 1)
        self.assertIn("Forex Factory is not responding", self.r.sent[0])
        self.assertIn("about 3 hours", self.r.sent[0])
        self.r.check({"forex_factory": 5}, at(10))
        self.assertEqual(len(self.r.sent), 2)
        self.assertIn("Forex Factory is back", self.r.sent[1])
        self.r.check({"forex_factory": 5}, at(11))
        self.assertEqual(len(self.r.sent), 2)

    def test_can_warn_again_after_a_new_outage(self):
        self.r.check({"biquote": 1}, T0)
        self.r.check({"biquote": None}, at(3))
        self.r.check({"biquote": 1}, at(4))
        self.r.check({"biquote": None}, at(8))
        self.assertEqual(len(self.r.sent), 3)   # down, back, down

    def test_source_that_never_worked_is_not_reported(self):
        self.r.check({"fmp": None}, T0)
        self.r.check({"fmp": None}, at(48))
        self.assertEqual(self.r.sent, [])

    def test_throttled_sources_are_ignored(self):
        self.r.check({"biquote": 1}, T0)
        self.r.check({"biquote": "throttled"}, at(5))
        self.assertEqual(self.r.sent, [])

    def test_failed_notice_is_retried_next_run(self):
        self.r.check({"forex_factory": 1}, T0)

        def broken(message):
            raise RuntimeError("telegram down")
        with contextlib.redirect_stdout(io.StringIO()):
            health.check(self.r.db, {"forex_factory": None}, at(4), broken)
        self.assertIsNone(self.r.db.warned_at("forex_factory"))
        self.r.check({"forex_factory": None}, at(5))
        self.assertEqual(len(self.r.sent), 1)

    def test_no_notifier_means_no_warning_but_no_crash(self):
        with contextlib.redirect_stdout(io.StringIO()):
            health.check(self.r.db, {"forex_factory": 1}, T0, None)
            health.check(self.r.db, {"forex_factory": None}, at(5), None)
        self.assertIsNone(self.r.db.warned_at("forex_factory"))

    def test_owner_notifier_only_uses_the_first_chat(self):
        class Fake:
            chat_ids = ["111", "-100222"]
            calls = []

            def send(self, message, chat_id=None):
                self.calls.append(chat_id)
        fake = Fake()
        health.owner_notifier(fake)("hi")
        self.assertEqual(fake.calls, ["111"])
        self.assertIsNone(health.owner_notifier(None))


class Fake(CalendarSource):
    def __init__(self, source_id, events):
        self.source_id, self._events = source_id, events

    def fetch_events(self):
        return self._events


class WiringTests(unittest.TestCase):
    def test_load_calendar_reports_an_outage(self):
        sent = []
        with open_storage("sqlite:///:memory:") as db, contextlib.redirect_stdout(io.StringIO()):
            svc.load_calendar(T0, [Fake("forex_factory", [])], db, ("forex_factory",),
                              14, 31, sent.append)
            svc.load_calendar(at(4), [Fake("forex_factory", None)], db, ("forex_factory",),
                              14, 31, sent.append)
        self.assertEqual(len(sent), 1)

    def test_load_calendar_works_without_a_notifier(self):
        with open_storage("sqlite:///:memory:") as db, contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(svc.load_calendar(T0, [Fake("forex_factory", None)], db,
                                                ("forex_factory",), 14, 31))


if __name__ == "__main__":
    unittest.main()
