"""Tests for the FMP and Finnhub sources and their shared helpers.
Run from the repo root:  python -m unittest discover -s tests -v"""
import contextlib
import io
import json
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
import merge as mg  # noqa: E402
from models import Event  # noqa: E402
from sources import CalendarSource, FinnhubSource, FmpSource  # noqa: E402
from sources import common, finnhub, fmp  # noqa: E402
from sources.http import get_json  # noqa: E402
from sources.registry import build_sources  # noqa: E402

KEY = "SECRET-KEY-123"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run(source, *responses):
    """Run fetch_events with canned responses; returns (events, printed, urlopen mock)."""
    out = io.StringIO()
    side = [r if isinstance(r, Exception) else FakeResponse(r) for r in responses]
    with mock.patch("urllib.request.urlopen", side_effect=side) as op, \
            mock.patch("time.sleep"), contextlib.redirect_stdout(out):
        events = source.fetch_events()
    return events, out.getvalue(), op


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


class HelperTests(unittest.TestCase):
    def test_currency_from_field_or_country_code(self):
        self.assertEqual(common.currency_from("usd", "XX"), "USD")
        self.assertEqual(common.currency_from("", "DE"), "EUR")
        self.assertEqual(common.currency_from(None, "gb"), "GBP")
        self.assertIsNone(common.currency_from("INR", "IN"))

    def test_impact_words(self):
        self.assertEqual(common.impact_from("High"), "high")
        self.assertEqual(common.impact_from("MEDIUM"), "medium")
        self.assertEqual(common.impact_from("None"), "")
        self.assertEqual(common.impact_from("Low", "Bank Holiday"), "holiday")

    def test_value_text(self):
        self.assertEqual(common.value_text(0.3, "%"), "0.3%")
        self.assertEqual(common.value_text(198.0, "K"), "198K")
        self.assertEqual(common.value_text("0.3%", "%"), "0.3%")
        self.assertIsNone(common.value_text(None, "%"))
        self.assertEqual(common.value_text(5), "5")

    def test_http_helper_never_prints_the_url(self):
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=http_error(403)), \
                contextlib.redirect_stdout(out):
            self.assertIsNone(get_json(f"https://x/?apikey={KEY}", "label", retries=3))
        self.assertIn("HTTP 403", out.getvalue())
        self.assertNotIn(KEY, out.getvalue())


class FmpTests(unittest.TestCase):
    REC = {"date": "2026-11-04 13:30:00", "country": "US", "event": "CPI (MoM) (Oct)",
           "currency": "USD", "previous": 0.2, "estimate": 0.3, "actual": None,
           "change": None, "impact": "High"}

    def src(self, **kw):
        return FmpSource(api_key=KEY, retries=1, clock=lambda: NOW, **kw)

    def test_record_converts(self):
        e = fmp.to_event(self.REC)
        self.assertEqual((e.source, e.country, e.title, e.impact),
                         ("fmp", "USD", "CPI (MoM)", "high"))
        self.assertEqual((e.previous, e.forecast, e.actual), ("0.2", "0.3", None))
        self.assertEqual(e.time, datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc))

    def test_reference_periods_are_dropped_from_titles(self):
        self.assertEqual(fmp.clean_title("GDP (QoQ) (Q3)"), "GDP (QoQ)")
        self.assertEqual(fmp.clean_title("Retail Sales (Oct)"), "Retail Sales")
        self.assertEqual(fmp.clean_title("Cash Rate"), "Cash Rate")

    def test_other_economies_and_bad_records_are_skipped(self):
        for bad in ({**self.REC, "country": "IN", "currency": "INR"}, {}, "junk", None,
                    {**self.REC, "date": None}, {**self.REC, "event": ""},
                    {**self.REC, "date": "garbage"}):
            self.assertIsNone(fmp.to_event(bad))

    def test_events_are_returned(self):
        events, out, _ = run(self.src(), [self.REC, {**self.REC, "country": "IN", "currency": "INR"}])
        self.assertEqual(len(events), 1)
        self.assertIn("1 events (1 skipped", out)

    def test_urls_have_dates_and_key_and_stable_comes_first(self):
        _, _, op = run(self.src(), [self.REC])
        url = op.call_args[0][0].full_url
        self.assertTrue(url.startswith("https://financialmodelingprep.com/stable/economic-calendar?"))
        self.assertIn("from=2026-11-01&", url)
        self.assertIn(f"&apikey={KEY}", url)

    def test_range_never_exceeds_the_90_day_limit(self):
        with mock.patch.object(cfg, "HORIZON_DAYS", 400):
            urls = self.src()._urls()
        self.assertIn("to=2027-02-01&", urls[0])      # 2026-11-03 + 90 days

    def test_falls_back_to_the_older_endpoint_when_the_first_is_empty_or_missing(self):
        events, _, op = run(self.src(), [], [self.REC])
        self.assertEqual(len(events), 1)
        self.assertIn("/api/v3/economic_calendar", op.call_args[0][0].full_url)
        events, _, _ = run(self.src(), http_error(404), [self.REC])
        self.assertEqual(len(events), 1)

    def test_both_endpoints_empty_is_reachable_but_empty(self):
        self.assertEqual(run(self.src(), [], [])[0], [])

    def test_refused_key_gives_none_and_never_leaks_it(self):
        events, out, _ = run(self.src(), http_error(402), http_error(402))
        self.assertIsNone(events)
        self.assertNotIn(KEY, out)

    def test_error_message_instead_of_a_list_gives_none(self):
        events, out, _ = run(self.src(), {"Error Message": "Limit Reach"}, {"Error Message": "x"})
        self.assertIsNone(events)
        self.assertIn("unexpected response", out)

    def test_without_a_key_nothing_is_requested(self):
        with mock.patch("urllib.request.urlopen") as op, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(FmpSource(api_key="").fetch_events())
        op.assert_not_called()
        self.assertIn("no FMP_API_KEY", out.getvalue())

    def test_key_from_environment(self):
        with mock.patch.dict(os.environ, {"FMP_API_KEY": " k "}):
            self.assertEqual(FmpSource().api_key, "k")


class FinnhubTests(unittest.TestCase):
    REC = {"actual": None, "country": "US", "estimate": 0.3, "event": "CPI MoM",
           "impact": "high", "prev": 0.2, "time": "2026-11-04 13:30:00", "unit": "%"}

    def src(self, **kw):
        return FinnhubSource(api_key=KEY, retries=1, clock=lambda: NOW, **kw)

    def test_record_converts_with_units(self):
        e = finnhub.to_event(self.REC)
        self.assertEqual((e.source, e.country, e.title, e.impact),
                         ("finnhub", "USD", "CPI MoM", "high"))
        self.assertEqual((e.previous, e.forecast, e.actual), ("0.2%", "0.3%", None))
        self.assertEqual(e.time, datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc))

    def test_bad_and_foreign_records_are_skipped(self):
        for bad in ({**self.REC, "country": "IN"}, {**self.REC, "time": None},
                    {**self.REC, "event": None}, {**self.REC, "time": "garbage"}, "x", None):
            self.assertIsNone(finnhub.to_event(bad))

    def test_wrapped_and_plain_list_responses_work(self):
        self.assertEqual(len(run(self.src(), {"economicCalendar": [self.REC]})[0]), 1)
        self.assertEqual(len(run(self.src(), [self.REC])[0]), 1)

    def test_events_outside_the_window_are_dropped(self):
        old = {**self.REC, "time": "2026-09-01 10:00:00"}
        far = {**self.REC, "time": "2027-06-01 10:00:00"}
        events, _, _ = run(self.src(), {"economicCalendar": [old, self.REC, far]})
        self.assertEqual(len(events), 1)

    def test_key_goes_in_a_header_not_the_url(self):
        _, _, op = run(self.src(), {"economicCalendar": []})
        request = op.call_args[0][0]
        self.assertNotIn(KEY, request.full_url)
        self.assertEqual(request.get_header("X-finnhub-token"), KEY)

    def test_refused_key_gives_none_and_never_leaks_it(self):
        events, out, _ = run(self.src(), http_error(403))
        self.assertIsNone(events)
        self.assertNotIn(KEY, out)

    def test_unexpected_response_gives_none(self):
        events, out, _ = run(self.src(), {"error": "You don't have access to this resource."})
        self.assertIsNone(events)
        self.assertIn("unexpected response", out)

    def test_without_a_key_nothing_is_requested(self):
        with mock.patch("urllib.request.urlopen") as op, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(FinnhubSource(api_key="").fetch_events())
        op.assert_not_called()
        self.assertIn("no FINNHUB_API_KEY", out.getvalue())


class RegistryAndMergeTests(unittest.TestCase):
    def test_all_four_sources_are_registered(self):
        names = [s.source_id for s in build_sources(
            ["forex_factory", "trading_economics", "fmp", "finnhub"])]
        self.assertEqual(names, ["forex_factory", "trading_economics", "fmp", "finnhub"])
        self.assertTrue(all(isinstance(s, CalendarSource) for s in build_sources(["fmp"])))

    def test_same_release_from_ff_fmp_and_finnhub_becomes_one_event(self):
        ff = Event(source="forex_factory", title="CPI m/m", country="USD",
                   time="2026-11-04T13:30:00+00:00", impact="high")
        a = fmp.to_event(FmpTests.REC)
        b = finnhub.to_event(FinnhubTests.REC)
        (out,) = mg.merge([b, a, ff])
        self.assertEqual(out.source, "forex_factory+fmp+finnhub")
        self.assertEqual((out.title, out.forecast), ("CPI m/m", "0.3"))


if __name__ == "__main__":
    unittest.main()
