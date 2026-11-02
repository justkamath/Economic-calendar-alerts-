"""Run from the repo root:  python -m unittest discover -s tests -v"""
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
from sources import CalendarSource, TradingEconomicsSource  # noqa: E402
from sources.registry import build_sources  # noqa: E402
from sources.trading_economics import to_event  # noqa: E402

KEY = "SECRET-KEY-123"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

# Record shape copied from Trading Economics' documentation
DOC_RECORD = {
    "CalendarId": "85653", "Date": "2016-12-02T00:30:00", "Country": "Australia",
    "Category": "Retail Sales MoM", "Event": "Retail Sales MoM", "Reference": "Oct",
    "Source": "Australian Bureau of Statistics", "Actual": "0.5%", "Previous": "0.6%",
    "Forecast": "0.3%", "TEForecast": "0.2%", "DateSpan": "0", "Importance": 2,
    "Revised": "", "Currency": "", "Unit": "%",
}


def rec(**kw):
    out = dict(DOC_RECORD)
    out.update(kw)
    return out


class FakeResponse:
    def __init__(self, payload):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def source(**kw):
    return TradingEconomicsSource(api_key=KEY, retries=1, clock=lambda: NOW, **kw)


class ToEventTests(unittest.TestCase):
    def test_documented_record_converts(self):
        e = to_event(DOC_RECORD)
        self.assertEqual((e.source, e.country, e.title, e.impact),
                         ("trading_economics", "AUD", "Retail Sales MoM", "medium"))
        self.assertEqual((e.previous, e.forecast, e.actual), ("0.6%", "0.3%", "0.5%"))

    def test_time_is_utc(self):
        self.assertEqual(to_event(DOC_RECORD).time,
                         datetime(2016, 12, 2, 0, 30, tzinfo=timezone.utc))

    def test_te_forecast_is_not_used(self):
        self.assertEqual(to_event(rec(Forecast="")).forecast, None)

    def test_importance_levels(self):
        self.assertEqual([to_event(rec(Importance=i)).impact for i in (1, 2, 3)],
                         ["low", "medium", "high"])
        self.assertEqual(to_event(rec(Importance="3")).impact, "high")
        self.assertEqual(to_event(rec(Importance=None)).impact, "")
        self.assertEqual(to_event(rec(Importance=0)).impact, "")

    def test_holidays_are_recognised(self):
        self.assertEqual(to_event(rec(Event="Bank Holiday", Importance=1)).impact, "holiday")

    def test_countries_map_to_currencies(self):
        for country, code in (("United States", "USD"), ("Euro Area", "EUR"),
                              ("Germany", "EUR"), ("United Kingdom", "GBP"),
                              ("Japan", "JPY"), ("New Zealand", "NZD"),
                              ("Canada", "CAD"), ("Switzerland", "CHF"), ("China", "CNY")):
            self.assertEqual(to_event(rec(Country=country)).country, code, country)

    def test_currency_field_wins_when_present(self):
        self.assertEqual(to_event(rec(Country="Somewhere", Currency="usd")).country, "USD")

    def test_other_economies_are_skipped(self):
        self.assertIsNone(to_event(rec(Country="India")))
        self.assertIsNone(to_event(rec(Country="Netherlands")))

    def test_bad_records_are_skipped(self):
        for bad in ({}, rec(Date=None), rec(Date="garbage"), rec(Event="", Category=""),
                    "junk", None):
            self.assertIsNone(to_event(bad))

    def test_falls_back_to_category_when_event_is_missing(self):
        self.assertEqual(to_event(rec(Event="", Category="Unemployment Rate")).title,
                         "Unemployment Rate")


class SourceTests(unittest.TestCase):
    def fetch(self, payload, src=None):
        buf = io.StringIO()
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)) as op, \
                contextlib.redirect_stdout(buf):
            events = (src or source()).fetch_events()
        return events, buf.getvalue(), op

    def test_is_a_calendar_source(self):
        self.assertIsInstance(source(), CalendarSource)
        self.assertEqual(source().source_id, "trading_economics")

    def test_events_are_returned_and_foreign_ones_skipped(self):
        events, out, _ = self.fetch([DOC_RECORD, rec(Country="India"), rec(Country="Japan")])
        self.assertEqual([e.country for e in events], ["AUD", "JPY"])
        self.assertIn("2 events (1 skipped", out)

    def test_request_url_has_dates_key_and_format(self):
        _, _, op = self.fetch([])
        end = (NOW + timedelta(days=cfg.STORE_AHEAD_DAYS)).date()
        self.assertEqual(
            op.call_args[0][0].full_url,
            f"https://api.tradingeconomics.com/calendar/country/All/2026-11-01/{end}"
            f"?c={KEY}&f=json")

    def test_guest_style_key_keeps_its_colon(self):
        src = TradingEconomicsSource(api_key="guest:guest", clock=lambda: NOW)
        self.assertIn("c=guest:guest&f=json", src._url())

    def test_without_a_key_nothing_is_requested(self):
        src = TradingEconomicsSource(api_key="", clock=lambda: NOW)
        with mock.patch("urllib.request.urlopen") as op, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(src.fetch_events())
        op.assert_not_called()
        self.assertIn("no TRADING_ECONOMICS_KEY", out.getvalue())

    def test_key_is_read_from_the_environment(self):
        with mock.patch.dict(os.environ, {"TRADING_ECONOMICS_KEY": " from-env "}):
            self.assertEqual(TradingEconomicsSource().api_key, "from-env")

    def test_rejected_key_gives_none_without_retrying_or_leaking_the_key(self):
        err = urllib.error.HTTPError("u", 401, "no", {}, None)
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=err) as op, \
                contextlib.redirect_stdout(out):
            self.assertIsNone(TradingEconomicsSource(api_key=KEY, retries=3,
                                                     clock=lambda: NOW).fetch_events())
        self.assertEqual(op.call_count, 1)
        self.assertIn("HTTP 401", out.getvalue())
        self.assertNotIn(KEY, out.getvalue())

    def test_server_error_is_retried_then_none(self):
        err = urllib.error.HTTPError("u", 500, "boom", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err) as op, \
                mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(TradingEconomicsSource(api_key=KEY, retries=3,
                                                     clock=lambda: NOW).fetch_events())
        self.assertEqual(op.call_count, 3)

    def test_network_error_and_bad_json_give_none_without_leaking_the_key(self):
        out = io.StringIO()
        with mock.patch("time.sleep"), contextlib.redirect_stdout(out):
            with mock.patch("urllib.request.urlopen",
                            side_effect=urllib.error.URLError("down")):
                self.assertIsNone(source().fetch_events())
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"<html>")):
                self.assertIsNone(source().fetch_events())
        self.assertNotIn(KEY, out.getvalue())

    def test_a_message_instead_of_a_list_gives_none(self):
        events, out, _ = self.fetch({"message": "No Access to this country as free user"})
        self.assertIsNone(events)
        self.assertIn("unexpected response", out)

    def test_empty_list_is_reachable_but_no_events(self):
        self.assertEqual(self.fetch([])[0], [])

    def test_registry_builds_it(self):
        names = [s.source_id for s in build_sources(["forex_factory", "trading_economics"])]
        self.assertEqual(names, ["forex_factory", "trading_economics"])


class MergeWithForexFactoryTests(unittest.TestCase):
    def test_same_release_from_both_sources_becomes_one_event(self):
        ff = Event(source="forex_factory", title="Retail Sales m/m", country="AUD",
                   time="2026-11-03T00:30:00+00:00", impact="high", forecast="0.3%")
        te = to_event(rec(Date="2026-11-03T00:30:00", Previous="0.6%", Forecast="0.4%"))
        (out,) = mg.merge([te, ff])
        self.assertEqual((out.title, out.impact, out.forecast, out.previous),
                         ("Retail Sales m/m", "high", "0.3%", "0.6%"))


if __name__ == "__main__":
    unittest.main()
