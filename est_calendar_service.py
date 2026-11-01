"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
import calendar_service as svc  # noqa: E402
import config as cfg  # noqa: E402
from models import Event  # noqa: E402
from sources import CalendarSource  # noqa: E402
from sources.registry import build_sources  # noqa: E402
from storage import open_storage  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PRIORITY = ("forex_factory", "trading_economics")


def ev(source, title, hours=1, country="AUD", **kw):
    return Event(source=source, title=title, country=country,
                 time=NOW + timedelta(hours=hours), **kw)


class FakeSource(CalendarSource):
    def __init__(self, source_id, events=None, boom=False):
        self.source_id, self._events, self._boom = source_id, events, boom

    def fetch_events(self):
        if self._boom:
            raise RuntimeError("exploded")
        return self._events


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = open_storage("sqlite:///:memory:")

    def tearDown(self):
        self.db.close()

    def load(self, sources, ahead=31, keep=14):
        return svc.load_calendar(NOW, sources, self.db, PRIORITY, keep, ahead)

    def test_sources_are_stored_and_merged_into_one_calendar(self):
        out = self.load([
            FakeSource("forex_factory", [ev("forex_factory", "CPI m/m", impact="high")]),
            FakeSource("trading_economics", [ev("trading_economics", "CPI MoM", forecast="0.7%")]),
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["title"], out[0]["impact"], out[0]["forecast"]),
                         ("CPI m/m", "High", "0.7%"))
        self.assertEqual(self.db.counts_by_source(),
                         {"forex_factory": 1, "trading_economics": 1})

    def test_a_source_that_raises_does_not_stop_the_others(self):
        out = self.load([FakeSource("forex_factory", boom=True),
                         FakeSource("trading_economics", [ev("trading_economics", "Cash Rate")])])
        self.assertEqual([e["title"] for e in out], ["Cash Rate"])

    def test_a_late_source_is_covered_by_the_others(self):
        # the Forex Factory problem: its feed has nothing for the new week yet
        out = self.load([FakeSource("forex_factory", None),
                         FakeSource("trading_economics", [ev("trading_economics", "Cash Rate")])])
        self.assertEqual(len(out), 1)

    def test_everything_down_still_uses_earlier_stored_events(self):
        self.load([FakeSource("forex_factory", [ev("forex_factory", "Cash Rate")])])
        out = self.load([FakeSource("forex_factory", None)])
        self.assertEqual([e["title"] for e in out], ["Cash Rate"])

    def test_everything_down_and_nothing_stored_gives_none(self):
        self.assertIsNone(self.load([FakeSource("forex_factory", None)]))

    def test_reachable_but_no_events_gives_an_empty_list(self):
        self.assertEqual(self.load([FakeSource("forex_factory", [])]), [])

    def test_no_sources_configured_gives_whatever_is_stored(self):
        self.db.upsert([ev("forex_factory", "Cash Rate")])
        self.assertEqual(len(self.load([])), 1)

    def test_old_events_are_pruned_and_far_ones_left_out(self):
        self.db.upsert([ev("forex_factory", "ancient", hours=-24 * 20),
                        ev("forex_factory", "soon", hours=2),
                        ev("forex_factory", "far", hours=24 * 40)])
        out = self.load([], ahead=31, keep=14)
        self.assertEqual([e["title"] for e in out], ["soon"])
        self.assertNotIn("ancient", [e.title for e in self.db.events_between(
            NOW - timedelta(days=60), NOW + timedelta(days=60))])

    def test_yesterday_is_still_available_for_the_daily_summary(self):
        self.db.upsert([ev("forex_factory", "earlier today", hours=-5)])
        self.assertEqual(len(self.load([])), 1)

    def test_result_works_with_the_alert_code(self):
        calendar = self.load([FakeSource("forex_factory",
                                         [ev("forex_factory", "Cash Rate", impact="high")])])
        self.assertEqual(len(alerts.select_upcoming(calendar, NOW, min_rank=0)), 1)

    def test_sync_reports_counts_and_none_for_unavailable(self):
        out = svc.sync([FakeSource("a", [ev("a", "x")]), FakeSource("b", None),
                        FakeSource("c", boom=True)], self.db)
        self.assertEqual(out, {"a": 1, "b": None, "c": None})


class FetchCalendarTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._names = ("USE_DATABASE", "DATABASE_URL", "CALENDAR_SOURCES",
                       "SOURCE_PRIORITY", "DB_KEEP_DAYS")
        self._old = {n: getattr(cfg, n, None) for n in self._names}
        self._ff, self._bs = svc.ForexFactorySource, svc.build_sources
        cfg.DATABASE_URL = f"sqlite:///{self._tmp.name}/calendar.db"
        cfg.CALENDAR_SOURCES, cfg.SOURCE_PRIORITY = ("forex_factory",), PRIORITY
        cfg.DB_KEEP_DAYS = 14

    def tearDown(self):
        for n, v in self._old.items():
            setattr(cfg, n, v)
        svc.ForexFactorySource, svc.build_sources = self._ff, self._bs
        self._tmp.cleanup()

    def test_database_off_does_the_old_direct_fetch(self):
        cfg.USE_DATABASE = False

        class Direct:
            def fetch_calendar(self):
                return [{"title": "direct"}]
        svc.ForexFactorySource = Direct
        self.assertEqual(svc.fetch_calendar(NOW), [{"title": "direct"}])
        self.assertFalse(os.path.exists(f"{self._tmp.name}/calendar.db"))

    def test_database_on_stores_and_returns_the_merged_calendar(self):
        cfg.USE_DATABASE = True
        svc.build_sources = lambda names: [
            FakeSource("forex_factory", [ev("forex_factory", "Cash Rate", impact="high")])]
        out = svc.fetch_calendar(NOW)
        self.assertEqual([e["title"] for e in out], ["Cash Rate"])
        self.assertTrue(os.path.exists(f"{self._tmp.name}/calendar.db"))

    def test_unopenable_database_falls_back_to_direct_fetch(self):
        cfg.USE_DATABASE = True
        cfg.DATABASE_URL = "postgres://nope"

        class Direct:
            def fetch_calendar(self):
                return [{"title": "direct"}]
        svc.ForexFactorySource = Direct
        self.assertEqual(svc.fetch_calendar(NOW), [{"title": "direct"}])


class RegistryTests(unittest.TestCase):
    def test_known_and_unknown_names(self):
        built = build_sources(["forex_factory", "nonsense"])
        self.assertEqual([s.source_id for s in built], ["forex_factory"])
        self.assertEqual(build_sources([]), [])


if __name__ == "__main__":
    unittest.main()
