"""Automatic expiry sync. No test touches the network: downloads are faked.
Run: python -m unittest discover -s tests -v"""
import gzip
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import expiry  # noqa: E402
import expiry_sync as sync  # noqa: E402

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 12)


def ms(y, m, d):
    """Upstox style expiry: milliseconds since 1970, at 23:59:59 India time."""
    return int(datetime(y, m, d, 23, 59, 59, tzinfo=expiry.IST).timestamp() * 1000)


def rec(segment, name, kind, expiry_date, **extra):
    base = {"segment": segment, "name": name, "underlying_symbol": name, "instrument_type": kind,
            "trading_symbol": f"{name} X", "expiry": ms(*expiry_date)}
    base.update(extra)
    return base


MCX = [
    rec("MCX_FO", "CRUDEOIL", "FUT", (2026, 11, 19)),
    rec("MCX_FO", "CRUDEOIL", "FUT", (2026, 12, 18)),
    rec("MCX_FO", "CRUDEOIL", "CE", (2026, 11, 17), strike_price=6000),
    rec("MCX_FO", "CRUDEOIL", "PE", (2026, 11, 17), strike_price=6100),      # same date: one row
    rec("MCX_FO", "CRUDEOILM", "FUT", (2026, 11, 19)),
    rec("MCX_FO", "NATURAL GAS", "FUT", (2026, 11, 24)),                      # name with a space
    rec("MCX_FO", "GOLD", "CE", (2026, 11, 27)),
    rec("MCX_FO", "GOLDM", "FUT", (2026, 12, 4)),
    rec("MCX_FO", "SILVER", "FUT", (2026, 12, 4)),
    rec("MCX_FO", "SILVERM", "FUT", (2026, 11, 30)),
    rec("MCX_FO", "COPPER", "FUT", (2026, 11, 30)),                           # not ours
    rec("MCX_FO", "CRUDEOIL", "FUT", (2026, 11, 5)),                          # already expired
    {"segment": "MCX_FO", "name": "GOLD", "instrument_type": "FUT", "expiry": None},   # broken row
]
NSE = [
    rec("NSE_FO", "NIFTY", "CE", (2026, 11, 17), weekly=True),
    rec("NSE_FO", "NIFTY", "PE", (2026, 11, 17), weekly=True),
    rec("NSE_FO", "NIFTY", "CE", (2026, 11, 23), weekly=False),
    rec("NSE_FO", "NIFTY", "FUT", (2026, 11, 23)),
    rec("NSE_FO", "BANKNIFTY", "FUT", (2026, 11, 23)),
    rec("NSE_FO", "MIDCPNIFTY", "FUT", (2026, 11, 23)),
    rec("NSE_FO", "RELIANCE", "FUT", (2026, 11, 23)),                         # stock: not synced
    rec("NSE_COM", "CRUDEOIL", "FUT", (2026, 11, 19)),
    rec("NSE_COM", "NATURALGAS", "CE", (2026, 11, 18)),
    rec("NSE_EQ", "NIFTY", "FUT", (2026, 11, 23)),                            # wrong segment
]


def fake_fetch(mcx=MCX, nse=NSE):
    def fetch(url):
        return gzip.compress(json.dumps(mcx if "MCX" in url else nse).encode())
    return fetch


class Parse(unittest.TestCase):
    def rows(self):
        return sync.parse(MCX + NSE, TODAY)

    def has(self, key, kind, day):
        return any(r["i"] == key and r["t"] == kind and r["d"] == day for r in self.rows())

    def test_commodities_futures_and_options(self):
        self.assertTrue(self.has("crude", "Futures", "2026-11-19"))
        self.assertTrue(self.has("crude", "Futures", "2026-12-18"))
        self.assertTrue(self.has("crude", "Options", "2026-11-17"))
        self.assertTrue(self.has("crudem", "Futures", "2026-11-19"))
        self.assertTrue(self.has("natgas", "Futures", "2026-11-24"))
        self.assertTrue(self.has("gold", "Options", "2026-11-27"))
        self.assertTrue(self.has("goldm", "Futures", "2026-12-04"))
        self.assertTrue(self.has("silverm", "Futures", "2026-11-30"))

    def test_nse_commodities_and_indices(self):
        self.assertTrue(self.has("nsecrude", "Futures", "2026-11-19"))
        self.assertTrue(self.has("nsenatgas", "Options", "2026-11-18"))
        self.assertTrue(self.has("nifty", "Weekly options", "2026-11-17"))
        self.assertTrue(self.has("nifty", "Monthly futures & options", "2026-11-23"))
        self.assertTrue(self.has("banknifty", "Monthly futures & options", "2026-11-23"))
        self.assertTrue(self.has("midcap", "Monthly futures & options", "2026-11-23"))

    def test_ignores_what_we_do_not_cover_and_bad_rows(self):
        keys = {r["i"] for r in self.rows()}
        self.assertEqual(keys, {"crude", "crudem", "natgas", "gold", "goldm", "silver", "silverm",
                                "nsecrude", "nsenatgas", "nifty", "banknifty", "midcap"})
        self.assertFalse(self.has("crude", "Futures", "2026-11-05"))          # expired
        self.assertEqual(len(self.rows()), len({(r["i"], r["t"], r["d"]) for r in self.rows()}))

    def test_epoch_converts_to_the_india_date(self):
        row = [r for r in self.rows() if r["i"] == "crudem"][0]
        self.assertEqual(row["d"], "2026-11-19")                              # not shifted by UTC
        self.assertTrue(all(r["listed"] for r in self.rows()))

    def test_other_name_fields_and_far_dates(self):
        odd = [{"segment": "MCX_FO", "name": "x", "underlying_symbol": "", "asset_symbol": "",
                "trading_symbol": "GOLDM FUT 05 JAN 27", "instrument_type": "FUT", "expiry": ms(2027, 1, 5)},
               rec("MCX_FO", "GOLD", "FUT", (2031, 1, 5))]
        rows = sync.parse(odd, TODAY)
        self.assertEqual([(r["i"], r["d"]) for r in rows], [("goldm", "2027-01-05")])


class Engine(unittest.TestCase):
    """Synced exact dates feed straight into the cards."""
    def test_index_exact_dates_replace_rule_dates_inside_their_window(self):
        data = {"nse_holidays": [], "holiday_years": [2026],
                "expiries": [{"i": "nifty", "t": "Weekly options", "d": "2026-11-18", "listed": True},
                             {"i": "nifty", "t": "Monthly futures & options", "d": "2026-11-26", "listed": True}]}
        rows = expiry.upcoming("nifty", date(2026, 11, 12), 8, data)
        weekly = [r["date"] for r in rows if r["kind"] == "Weekly options"]
        monthly = [r["date"] for r in rows if r["kind"].startswith("Monthly")]
        self.assertEqual(weekly[0], date(2026, 11, 18))            # exact, not the rule's Tuesday
        self.assertEqual(monthly[0], date(2026, 11, 26))
        self.assertTrue(all(not r["est"] for r in rows if r["date"] <= date(2026, 11, 26)))
        self.assertTrue(weekly[1] > date(2026, 11, 18) and rows[-1]["est"])   # beyond the listing: estimates

    def test_default_load_merges_holidays_and_synced_dates(self):
        data = expiry.load_data()
        self.assertIn("nse_holidays", data)
        self.assertTrue(data["expiries"])


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "expiry_synced.json")
        self.addCleanup(self.tmp.cleanup)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_sync_writes_the_file_and_reports(self):
        result = sync.sync(NOW, fake_fetch(), self.path)
        data = self.read()
        self.assertEqual(result["rows"], len(data["expiries"]))
        self.assertEqual(data["synced_at"], NOW.isoformat())
        self.assertIn("crude", result["covered"])
        self.assertIn("finnifty", result["missing"])               # not in the fake file: reported
        self.assertIn("next50", result["missing"])

    def test_missing_instruments_keep_their_old_dates_and_past_dates_go(self):
        with open(self.path, "w") as f:
            json.dump({"expiries": [
                {"i": "next50", "t": "Monthly futures & options", "d": "2026-11-23", "listed": True},
                {"i": "next50", "t": "Monthly futures & options", "d": "2026-10-27", "listed": True},
                {"i": "crude", "t": "Futures", "d": "2026-11-22", "listed": True}]}, f)
        sync.sync(NOW, fake_fetch(), self.path)
        rows = self.read()["expiries"]
        self.assertTrue(any(r["i"] == "next50" and r["d"] == "2026-11-23" for r in rows))
        self.assertFalse(any(r["d"] == "2026-10-27" for r in rows))        # in the past
        self.assertFalse(any(r["i"] == "crude" and r["d"] == "2026-11-22" for r in rows))   # replaced

    def test_a_broken_file_is_refused_and_leaves_the_old_data(self):
        with open(self.path, "w") as f:
            json.dump({"expiries": [{"i": "gold", "t": "Futures", "d": "2026-12-04", "listed": True}]}, f)
        for bad in (lambda url: b"not gzip", lambda url: gzip.compress(b"{}"),
                    fake_fetch(mcx=[], nse=[])):
            with self.assertRaises(Exception):
                sync.sync(NOW, bad, self.path)
        self.assertEqual(self.read()["expiries"][0]["i"], "gold")

    def test_maybe_sync_runs_once_a_day(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return fake_fetch()(url)

        self.assertIsNotNone(sync.maybe_sync(NOW, None, fetch, self.path))
        self.assertEqual(len(calls), 2)                                    # MCX and NSE files
        self.assertIsNone(sync.maybe_sync(NOW + timedelta(hours=5), None, fetch, self.path))
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(sync.maybe_sync(NOW + timedelta(hours=25), None, fetch, self.path))
        self.assertEqual(len(calls), 4)

    def test_failure_keeps_old_dates_notifies_once_and_retries_later(self):
        class Bot:
            sent = []

            def send(self, message, **kw):
                self.sent.append(message)

        bot = Bot()
        with open(self.path, "w") as f:
            json.dump({"synced_at": (NOW - timedelta(days=2)).isoformat(),
                       "attempted_at": (NOW - timedelta(days=2)).isoformat(),
                       "expiries": [{"i": "gold", "t": "Futures", "d": "2026-12-04", "listed": True}]}, f)

        def boom(url):
            raise OSError("network down")

        self.assertIsNone(sync.maybe_sync(NOW, bot, boom, self.path))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("could not refresh", bot.sent[0])
        self.assertEqual(self.read()["expiries"][0]["i"], "gold")          # old dates kept
        self.assertIsNone(sync.maybe_sync(NOW + timedelta(hours=1), bot, boom, self.path))   # too soon to retry
        sync.maybe_sync(NOW + timedelta(hours=7), bot, boom, self.path)    # retried, but no second notice today
        self.assertEqual(len(bot.sent), 1)
        sync.maybe_sync(NOW + timedelta(hours=31), bot, boom, self.path)   # a day later: notify again
        self.assertEqual(len(bot.sent), 2)
        self.assertIsNotNone(sync.maybe_sync(NOW + timedelta(hours=40), bot, fake_fetch(), self.path))

    def test_sync_can_be_switched_off(self):
        os.environ["EXPIRY_SYNC"] = "off"
        try:
            self.assertIsNone(sync.maybe_sync(NOW, None, fake_fetch(), self.path))
            self.assertFalse(os.path.exists(self.path))
        finally:
            del os.environ["EXPIRY_SYNC"]

    def test_notifier_failure_does_not_break_the_run(self):
        class Bad:
            def send(self, *a, **k):
                raise RuntimeError("telegram down")

        def boom(url):
            raise OSError("x")

        self.assertIsNone(sync.maybe_sync(NOW, Bad(), boom, self.path))


if __name__ == "__main__":
    unittest.main()
