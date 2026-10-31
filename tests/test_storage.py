"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
from models import Event  # noqa: E402
from storage import StorageError, open_storage  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def ev(minutes=0, source="forex_factory", title="Cash Rate", country="AUD", **kw):
    return Event(source=source, title=title, country=country,
                 time=T0 + timedelta(minutes=minutes), **kw)


class EventTests(unittest.TestCase):
    def test_values_are_normalised(self):
        e = Event(source=" ff ", title="  CPI ", country=" usd", impact="HIGH",
                  time="2026-11-03T08:00:00-04:00", previous=" 0.3% ",
                  forecast="", actual="n/a")
        self.assertEqual((e.source, e.title, e.country), ("ff", "CPI", "usd"))
        self.assertEqual(e.impact, "high")
        self.assertEqual(e.time, datetime(2026, 11, 3, 12, 0, tzinfo=UTC))
        self.assertEqual((e.previous, e.forecast, e.actual), ("0.3%", None, None))

    def test_unknown_impact_becomes_empty(self):
        self.assertEqual(ev(impact="Whatever").impact, "")
        self.assertEqual(ev(impact=None).impact, "")

    def test_naive_time_is_treated_as_utc(self):
        e = Event(source="s", title="t", country="USD", time=datetime(2026, 1, 1, 9, 0))
        self.assertEqual(e.time.tzinfo, UTC)

    def test_invalid_time_is_rejected(self):
        with self.assertRaises(ValueError):
            Event(source="s", title="t", country="USD", time="not a time")

    def test_key_matches_the_alert_state_key(self):
        e = ev(title="Cash Rate", country="AUD")
        raw = e.to_calendar_dict()
        self.assertEqual(e.key, alerts.event_key(raw, e.time))

    def test_calendar_dict_round_trip(self):
        e = ev(impact="medium", previous="1%", forecast="2%")
        back = Event.from_calendar_dict(e.to_calendar_dict(), "forex_factory")
        self.assertEqual(back, e)

    def test_from_calendar_dict_without_a_time_gives_none(self):
        self.assertIsNone(Event.from_calendar_dict({"title": "x"}, "s"))
        self.assertIsNone(Event.from_calendar_dict("junk", "s"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.db = open_storage("sqlite:///:memory:")

    def tearDown(self):
        self.db.close()

    def between(self, a=-10_000, b=10_000, **kw):
        return self.db.events_between(T0 + timedelta(minutes=a),
                                      T0 + timedelta(minutes=b), **kw)

    def test_round_trip(self):
        e = ev(impact="high", previous="4.60%", forecast="4.85%")
        self.assertEqual(self.db.upsert([e]), 1)
        self.assertEqual(self.between(), [e])

    def test_range_start_included_end_excluded_and_ordered(self):
        self.db.upsert([ev(60, title="C"), ev(0, title="A"), ev(30, title="B")])
        self.assertEqual([e.title for e in self.between(0, 60)], ["A", "B"])
        self.assertEqual([e.title for e in self.between()], ["A", "B", "C"])

    def test_same_event_from_two_sources_is_kept_per_source(self):
        self.db.upsert([ev(source="forex_factory"), ev(source="fmp")])
        self.assertEqual(len(self.between()), 2)
        self.assertEqual(len(self.between(sources=["fmp"])), 1)
        self.assertEqual(self.db.counts_by_source(), {"forex_factory": 1, "fmp": 1})

    def test_upsert_twice_does_not_duplicate(self):
        self.db.upsert([ev()])
        self.db.upsert([ev()])
        self.assertEqual(len(self.between()), 1)

    def test_new_values_fill_in_and_empty_ones_never_erase(self):
        self.db.upsert([ev(impact="high", forecast="1%")])
        self.db.upsert([ev(impact="", forecast=None, actual="1.2%")])  # actual arrives
        (stored,) = self.between()
        self.assertEqual((stored.impact, stored.forecast, stored.actual),
                         ("high", "1%", "1.2%"))
        self.db.upsert([ev(forecast="1.1%")])                           # revised
        self.assertEqual(self.between()[0].forecast, "1.1%")

    def test_prune_removes_only_old_events(self):
        self.db.upsert([ev(-120, title="old"), ev(0, title="new")])
        self.assertEqual(self.db.prune(T0 - timedelta(minutes=60)), 1)
        self.assertEqual([e.title for e in self.between()], ["new"])

    def test_works_with_the_existing_alert_code(self):
        self.db.upsert([ev(60, impact="high")])
        calendar = [e.to_calendar_dict() for e in self.between()]
        upcoming = alerts.select_upcoming(calendar, T0, min_rank=0)
        self.assertEqual(len(upcoming), 1)


class FactoryTests(unittest.TestCase):
    def test_file_survives_reopening_and_creates_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sqlite:///{tmp}/nested/dir/calendar.db"
            with open_storage(url) as db:
                db.upsert([ev()])
            with open_storage(url) as db:
                self.assertEqual(db.counts_by_source(), {"forex_factory": 1})

    def test_absolute_path_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open_storage(f"sqlite:///{tmp}/a.db"):   # 'sqlite:///' + '/tmp/..'
                self.assertTrue(os.path.exists(f"{tmp}/a.db"))

    def test_schema_version_is_recorded_and_reopen_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sqlite:///{tmp}/v.db"
            open_storage(url).close()
            with open_storage(url) as db:
                version = db._db.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 1)

    def test_unsupported_urls_give_a_clear_error(self):
        for bad in ("postgres://u:p@host/db", "", None, "nonsense"):
            with self.assertRaises(StorageError):
                open_storage(bad)

    def test_unopenable_path_gives_a_clear_error(self):
        with self.assertRaises(StorageError):
            open_storage("sqlite:////proc/definitely/not/writable.db")


if __name__ == "__main__":
    unittest.main()
