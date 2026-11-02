"""Run from the repo root:  python -m unittest discover -s tests -v"""
import contextlib
import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar_service as svc  # noqa: E402
import config as cfg  # noqa: E402
from models import Event  # noqa: E402
from sources import BiquoteSource, CalendarSource  # noqa: E402
from sources.biquote import to_event  # noqa: E402
from sources.registry import build_sources  # noqa: E402
from storage import open_storage  # noqa: E402

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

# Record copied from biquote's documentation
DOC = {"eventId": "mql5:840220001", "time": "2026-08-17T20:00:00Z", "countryCode": "US",
       "currency": "USD", "name": "TIC Net Long-Term Transactions", "importance": "medium",
       "type": "indicator", "unit": "currency", "multiplier": "billions", "actual": 172.7,
       "forecast": 90.5, "previous": 232.7, "revisedPrevious": 231.2, "timeMode": "exact"}


def rec(**kw):
    out = dict(DOC)
    out.update(kw)
    return out


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fetch(payload):
    out = io.StringIO()
    src = BiquoteSource(retries=1, clock=lambda: NOW)
    with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)) as op, \
            contextlib.redirect_stdout(out):
        return src.fetch_events(), out.getvalue(), op


class ToEventTests(unittest.TestCase):
    def test_documented_record_converts(self):
        e = to_event(DOC)
        self.assertEqual((e.source, e.country, e.title, e.impact),
                         ("biquote", "USD", "TIC Net Long-Term Transactions", "medium"))
        self.assertEqual((e.previous, e.forecast, e.actual), ("232.7B", "90.5B", "172.7B"))
        self.assertEqual(e.time, datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc))

    def test_percent_and_plain_numbers(self):
        e = to_event(rec(unit="percent", multiplier=None, actual=0.4, forecast=None, previous=0.2))
        self.assertEqual((e.actual, e.forecast, e.previous), ("0.4%", None, "0.2%"))
        e = to_event(rec(unit="index", multiplier=None, actual=55.8))
        self.assertEqual(e.actual, "55.8")

    def test_holidays(self):
        self.assertEqual(to_event(rec(type="holiday", importance="low")).impact, "holiday")
        self.assertEqual(to_event(rec(name="Bank Holiday")).impact, "holiday")

    def test_importance_levels(self):
        self.assertEqual([to_event(rec(importance=i)).impact for i in ("low", "medium", "high")],
                         ["low", "medium", "high"])
        self.assertEqual(to_event(rec(importance="whatever")).impact, "")

    def test_placeholder_times_are_skipped(self):
        self.assertIsNone(to_event(rec(timeMode="dateOnly")))
        self.assertIsNotNone(to_event({k: v for k, v in DOC.items() if k != "timeMode"}))

    def test_other_economies_and_bad_records_are_skipped(self):
        for bad in (rec(currency="INR", countryCode="IN"), rec(name=""), rec(time=None),
                    rec(time="garbage"), {}, "junk", None):
            self.assertIsNone(to_event(bad))

    def test_country_code_is_used_when_currency_is_missing(self):
        self.assertEqual(to_event(rec(currency=None, countryCode="DE")).country, "EUR")


class SourceTests(unittest.TestCase):
    def test_basics(self):
        self.assertIsInstance(BiquoteSource(), CalendarSource)
        self.assertEqual(BiquoteSource.source_id, "biquote")

    def test_no_key_needed_and_events_returned(self):
        events, out, _ = fetch([DOC, rec(currency="INR", countryCode="IN"),
                                rec(timeMode="dateOnly")])
        self.assertEqual(len(events), 1)
        self.assertIn("1 events (2 skipped", out)

    def test_url_has_the_date_range(self):
        _, _, op = fetch([])
        end = (NOW + timedelta(days=min(cfg.STORE_AHEAD_DAYS, 90))).date()
        self.assertEqual(op.call_args[0][0].full_url,
                         f"https://biquote.io/api/calendar?from=2026-11-01&to={end}")

    def test_wrapped_responses_work(self):
        for key in ("events", "data", "items", "results"):
            self.assertEqual(len(fetch({key: [DOC]})[0]), 1, key)

    def test_unexpected_response_gives_none(self):
        events, out, _ = fetch({"error": "nope"})
        self.assertIsNone(events)
        self.assertIn("unexpected response", out)

    def test_unreachable_gives_none(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 429, "x", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(BiquoteSource(retries=1, clock=lambda: NOW).fetch_events())

    def test_empty_list_is_reachable_but_empty(self):
        self.assertEqual(fetch([])[0], [])

    def test_registry_builds_it(self):
        self.assertEqual([s.source_id for s in build_sources(["biquote"])], ["biquote"])


class ServiceWiringTests(unittest.TestCase):
    class Fake(CalendarSource):
        def __init__(self, source_id, events):
            self.source_id, self._events = source_id, events

        def fetch_events(self):
            return self._events

    def test_biquote_fills_a_day_forex_factory_has_not_published_yet(self):
        def ev(source, title, hours):
            return Event(source=source, title=title, country="AUD", impact="high",
                         time=NOW + timedelta(hours=hours))
        ff = self.Fake("forex_factory", [ev("forex_factory", "CPI m/m", 1)])
        bq = self.Fake("biquote", [ev("biquote", "Some Other Name", 2),   # same day as FF
                                   ev("biquote", "Cash Rate", 30)])        # FF has nothing
        with open_storage("sqlite:///:memory:") as db:
            out = svc.load_calendar(NOW, [ff, bq], db, ("forex_factory",), 14, 31)
        self.assertEqual([e["title"] for e in out], ["CPI m/m", "Cash Rate"])

    def test_biquote_is_a_gap_filler_asked_every_15_minutes(self):
        self.assertIn("biquote", svc.GAP_FILL_SOURCES)
        self.assertEqual(svc.interval_for(BiquoteSource()), timedelta(minutes=15))


if __name__ == "__main__":
    unittest.main()
