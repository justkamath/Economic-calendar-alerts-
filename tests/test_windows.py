"""Storing far ahead, alerting only inside the weekly window.
Run from the repo root:  python -m unittest discover -s tests -v"""
import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
import calendar_service as svc  # noqa: E402
import config as cfg  # noqa: E402
from models import Event  # noqa: E402
from sources import CalendarSource  # noqa: E402
from storage import open_storage  # noqa: E402

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def ev(days, title="CPI"):
    return Event(source="forex_factory", title=f"{title} +{days}d", country="USD",
                 impact="high", time=NOW + timedelta(days=days))


class Fake(CalendarSource):
    source_id = "forex_factory"

    def fetch_events(self):
        return [ev(1), ev(6), ev(10), ev(40), ev(80)]


class WindowTests(unittest.TestCase):
    def test_defaults_are_sane(self):
        self.assertIn(cfg.ALERT_WEEKS, (1, 2))
        self.assertEqual(cfg.HORIZON_DAYS, 7 * cfg.ALERT_WEEKS)
        self.assertGreaterEqual(cfg.STORE_AHEAD_DAYS, cfg.HORIZON_DAYS)
        self.assertGreaterEqual(cfg.DB_KEEP_DAYS, 14)

    def load(self):
        with open_storage("sqlite:///:memory:") as db, contextlib.redirect_stdout(io.StringIO()):
            calendar = svc.load_calendar(NOW, [Fake()], db, ("forex_factory",), 365, 8)
            stored = db.events_between(NOW, NOW + timedelta(days=365))
        return calendar, stored

    def test_everything_is_stored_even_far_ahead(self):
        _, stored = self.load()
        self.assertEqual(len(stored), 5)

    def test_one_week_window_alerts_only_the_next_seven_days(self):
        calendar, _ = self.load()
        with mock.patch.object(cfg, "HORIZON_DAYS", 7):
            titles = [e["title"] for _, e, _ in alerts.select_upcoming(calendar, NOW)]
        self.assertEqual(sorted(titles), ["CPI +1d", "CPI +6d"])

    def test_two_week_window_adds_the_second_week(self):
        far = [e.to_calendar_dict() for e in Fake().fetch_events()]
        with mock.patch.object(cfg, "HORIZON_DAYS", 14):
            titles = [e["title"] for _, e, _ in alerts.select_upcoming(far, NOW)]
        self.assertEqual(sorted(titles), ["CPI +10d", "CPI +1d", "CPI +6d"])

    def test_old_events_are_kept_for_the_history_period(self):
        with open_storage("sqlite:///:memory:") as db, contextlib.redirect_stdout(io.StringIO()):
            db.upsert([ev(-200), ev(-400)])
            db.prune(NOW - timedelta(days=365))
            kept = db.events_between(NOW - timedelta(days=500), NOW)
        self.assertEqual([e.title for e in kept], ["CPI +-200d"])


class SourceRangeTests(unittest.TestCase):
    def test_sources_ask_for_the_storage_range_not_the_alert_window(self):
        from sources.biquote import BiquoteSource
        from sources.trading_economics import TradingEconomicsSource
        with mock.patch.object(cfg, "HORIZON_DAYS", 7), \
                mock.patch.object(cfg, "STORE_AHEAD_DAYS", 60):
            biquote = BiquoteSource(clock=lambda: NOW)._url()
            te = TradingEconomicsSource("k", clock=lambda: NOW)._url()
        self.assertTrue(biquote.endswith("to=2027-01-02"), biquote)   # NOW + 60 days
        self.assertIn("/2027-01-02?", te)


if __name__ == "__main__":
    unittest.main()
