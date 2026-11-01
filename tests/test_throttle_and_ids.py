"""Fetch throttling and stable event identities.
Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
import calendar_service as svc  # noqa: E402
import merge as mg  # noqa: E402
from models import Event  # noqa: E402
from sources import CalendarSource  # noqa: E402
from storage import open_storage  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PRIORITY = ("forex_factory", "trading_economics")


def ev(source, title, hours=1, country="AUD", impact="high", **kw):
    return Event(source=source, title=title, country=country,
                 time=NOW + timedelta(hours=hours), impact=impact, **kw)


class CountingSource(CalendarSource):
    def __init__(self, source_id, events=None, interval=None):
        self.source_id, self._events, self.calls = source_id, events, 0
        if interval is not None:
            self.min_interval_minutes = interval

    def fetch_events(self):
        self.calls += 1
        return self._events


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.db = open_storage("sqlite:///:memory:")

    def tearDown(self):
        self.db.close()

    def sync(self, sources, minutes=0):
        return svc.sync(sources, self.db, NOW + timedelta(minutes=minutes))

    def test_keyed_sources_are_asked_at_most_every_30_minutes(self):
        src = CountingSource("fmp", [ev("fmp", "CPI m/m")])
        self.assertEqual(self.sync([src], 0), {"fmp": 1})
        self.assertEqual(self.sync([src], 5), {"fmp": svc.THROTTLED})
        self.assertEqual(self.sync([src], 29), {"fmp": svc.THROTTLED})
        self.assertEqual(self.sync([src], 30), {"fmp": 1})
        self.assertEqual(src.calls, 2)

    def test_forex_factory_is_asked_every_run(self):
        src = CountingSource("forex_factory", [])
        for minute in (0, 5, 10):
            self.sync([src], minute)
        self.assertEqual(src.calls, 3)

    def test_a_refused_source_is_not_hammered(self):
        src = CountingSource("finnhub", None)               # returns None = refused
        self.assertEqual(self.sync([src], 0), {"finnhub": None})
        self.assertEqual(self.sync([src], 5), {"finnhub": svc.THROTTLED})
        self.assertEqual(src.calls, 1)

    def test_a_source_can_set_its_own_interval(self):
        src = CountingSource("custom", [], interval=10)
        self.sync([src], 0)
        self.assertEqual(self.sync([src], 9), {"custom": svc.THROTTLED})
        self.assertEqual(self.sync([src], 10), {"custom": 0})

    def test_sources_are_throttled_independently(self):
        a, b = CountingSource("fmp", []), CountingSource("finnhub", [])
        self.sync([a], 0)
        out = self.sync([a, b], 5)
        self.assertEqual(out, {"fmp": svc.THROTTLED, "finnhub": 0})

    def test_throttled_sources_do_not_count_as_unreachable(self):
        src = CountingSource("fmp", [ev("fmp", "Cash Rate")])
        svc.load_calendar(NOW, [src], self.db, PRIORITY, 14, 31)
        later = NOW + timedelta(minutes=5)
        out = svc.load_calendar(later, [src], self.db, PRIORITY, 14, 31)
        self.assertEqual([e["title"] for e in out], ["Cash Rate"])
        self.assertEqual(src.calls, 1)

    def test_interval_table(self):
        self.assertEqual(svc.interval_for(CountingSource("fmp")), timedelta(minutes=30))
        self.assertEqual(svc.interval_for(CountingSource("forex_factory")), timedelta(0))


class AliasTests(unittest.TestCase):
    def test_merged_events_carry_every_sources_key(self):
        ff, te = ev("forex_factory", "CPI m/m"), ev("trading_economics", "CPI MoM")
        (out,) = mg.merge([ff, te])
        self.assertEqual(out.aliases, (ff.key, te.key))
        self.assertEqual(out.to_calendar_dict()["ids"], [ff.key, te.key])

    def test_single_source_events_have_no_aliases(self):
        (out,) = mg.merge([ev("forex_factory", "CPI m/m")])
        self.assertEqual(out.aliases, ())
        self.assertNotIn("ids", out.to_calendar_dict())


class StableKeyTests(unittest.TestCase):
    def select(self, calendar, state=None):
        return alerts.select_upcoming(calendar, NOW, min_rank=0, state=state)

    def merged_calendar(self, *events):
        return [e.to_calendar_dict() for e in mg.merge(list(events))]

    def test_key_defaults_to_the_events_own_key(self):
        e = ev("forex_factory", "CPI m/m")
        ((key, _, _),) = self.select(self.merged_calendar(e))
        self.assertEqual(key, e.key)

    def test_a_slow_source_arriving_later_does_not_change_the_key(self):
        te = ev("trading_economics", "CPI MoM")
        ff = ev("forex_factory", "CPI m/m")

        # Run 1: only Trading Economics has it, so it is announced under TE's key.
        ((key1, _, _),) = self.select(self.merged_calendar(te))
        state = {key1: {"detected": True, "1h": False, "30m": False}}

        # Run 2: Forex Factory appears; the merged title is now Forex Factory's.
        merged = self.merged_calendar(te, ff)
        self.assertEqual(merged[0]["title"], "CPI m/m")
        ((key2, _, _),) = self.select(merged, state)
        self.assertEqual(key2, key1)                 # same identity: no repeat alert

    def test_without_history_the_default_key_is_used(self):
        te, ff = ev("trading_economics", "CPI MoM"), ev("forex_factory", "CPI m/m")
        ((key, _, _),) = self.select(self.merged_calendar(te, ff), {})
        self.assertEqual(key, ff.key)

    def test_plain_events_ignore_the_state_argument(self):
        e = ev("forex_factory", "CPI m/m")
        ((key, _, _),) = self.select([e.to_calendar_dict()], {"other|x|y": {}})
        self.assertEqual(key, e.key)

    def test_malformed_ids_are_ignored(self):
        d = ev("forex_factory", "CPI m/m").to_calendar_dict()
        d["ids"] = "not a list"
        ((key, _, _),) = self.select([d], {"a": {}})
        self.assertEqual(key, ev("forex_factory", "CPI m/m").key)


if __name__ == "__main__":
    unittest.main()
