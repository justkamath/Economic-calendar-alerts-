"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import CalendarSource, ForexFactorySource, events_from_calendar  # noqa: E402

# A few entries copied from the real Forex Factory feed
REAL = [
    {"title": "Bank Holiday", "country": "NZD", "date": "2026-10-25T15:00:00-04:00",
     "impact": "Holiday", "forecast": "", "previous": ""},
    {"title": "CPI m/m", "country": "AUD", "date": "2026-10-27T20:30:00-04:00",
     "impact": "High", "forecast": "0.7%", "previous": "0.4%"},
    {"title": "German 10-y Bond Auction", "country": "EUR",
     "date": "2026-10-28T06:32:00-04:00", "impact": "Low", "forecast": "",
     "previous": "3.58|1.2"},
]


def source_with(feeds):
    src = ForexFactorySource(urls=list(feeds))
    src._fetch_one = lambda url: feeds[url]
    return src


class ForexFactoryEventsTests(unittest.TestCase):
    def test_is_a_calendar_source_with_a_stable_id(self):
        src = source_with({"u": []})
        self.assertIsInstance(src, CalendarSource)
        self.assertEqual(src.source_id, "forex_factory")

    def test_real_feed_entries_become_events(self):
        events = source_with({"u": REAL}).fetch_events()
        self.assertEqual(len(events), 3)
        holiday, cpi, bond = events
        self.assertEqual((holiday.impact, holiday.country), ("holiday", "NZD"))
        self.assertEqual((cpi.impact, cpi.forecast, cpi.previous), ("high", "0.7%", "0.4%"))
        self.assertEqual(cpi.time, datetime(2026, 10, 28, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(bond.previous, "3.58|1.2")        # odd values are kept as text
        self.assertTrue(all(e.source == "forex_factory" for e in events))

    def test_events_round_trip_to_the_old_feed_shape(self):
        events = source_with({"u": REAL}).fetch_events()
        self.assertEqual(events[1].to_calendar_dict()["impact"], "High")

    def test_unreachable_source_gives_none_not_an_empty_list(self):
        self.assertIsNone(source_with({"u1": None, "u2": None}).fetch_events())

    def test_reachable_but_empty_gives_an_empty_list(self):
        self.assertEqual(source_with({"u": []}).fetch_events(), [])

    def test_entries_without_a_valid_time_are_skipped(self):
        bad = [{"title": "x", "country": "USD", "date": "garbage"},
               {"title": "y", "country": "USD"}, "junk"]
        events = source_with({"u": bad + REAL}).fetch_events()
        self.assertEqual(len(events), 3)

    def test_old_fetch_calendar_still_works(self):
        self.assertEqual(source_with({"u": REAL}).fetch_calendar(), REAL)


class HelperTests(unittest.TestCase):
    def test_events_from_calendar_counts_skipped(self):
        events, skipped = events_from_calendar(REAL + [{"date": "nope"}], "x")
        self.assertEqual((len(events), skipped), (3, 1))

    def test_a_source_must_implement_fetch_events(self):
        with self.assertRaises(TypeError):
            CalendarSource()


if __name__ == "__main__":
    unittest.main()
