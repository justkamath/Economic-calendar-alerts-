"""Expiry dates, the picker and the reminders. Run: python -m unittest discover -s tests -v"""
import os
import re
import sys
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import commands  # noqa: E402
import expiry  # noqa: E402
import expiry_reminders as reminders  # noqa: E402
from subscribers import Audience, SubscriberStore  # noqa: E402

KEY = "a-long-random-secret-key-123456"
DATA = {"holiday_years": [2026],
        "nse_holidays": ["2026-11-10", "2026-11-24"], "mcx_holidays": ["2026-12-25"],
        "mcx_expiries": [{"i": "crude", "t": "Futures", "d": "2026-11-19", "listed": True}]}


def utc(y, m, d, hh, mm=0):
    """A UTC moment given in IST clock time."""
    from datetime import timedelta
    return datetime(y, m, d, hh, mm, tzinfo=expiry.IST).astimezone(timezone.utc)


class NseRules(unittest.TestCase):
    def rows(self, key, today=date(2026, 11, 1), count=6):
        return expiry.upcoming(key, today, count, DATA)

    def test_nifty_weekly_is_tuesday(self):
        weekly = [r for r in self.rows("nifty", date(2026, 11, 11)) if r["kind"] == "Weekly options"]
        self.assertEqual(weekly[0]["date"], date(2026, 11, 17))
        self.assertTrue(all(r["date"].weekday() == 1 for r in weekly))

    def test_holiday_moves_expiry_to_the_previous_trading_day(self):
        rows = self.rows("nifty", date(2026, 11, 2))
        self.assertEqual(rows[0]["date"], date(2026, 11, 3))
        self.assertEqual(rows[1]["date"], date(2026, 11, 9))     # Tue 10 Nov is a holiday

    def test_monthly_is_last_tuesday_or_the_day_before_a_holiday(self):
        monthly = [r for r in self.rows("banknifty") if "Monthly" in r["kind"]]
        self.assertEqual(monthly[0]["date"], date(2026, 11, 23))  # Tue 24 Nov is a holiday
        self.assertEqual(monthly[1]["date"], date(2026, 12, 29))

    def test_monthly_day_is_not_repeated_as_weekly(self):
        days = [r["date"] for r in self.rows("nifty", count=20)]
        self.assertEqual(len(days), len(set(days)))

    def test_only_nifty_has_weekly_and_all_nse_names_have_dates(self):
        for key in ("banknifty", "finnifty", "midcap", "next50", "stockfo"):
            self.assertTrue(all("Monthly" in r["kind"] for r in self.rows(key)), key)
        for key in expiry.ORDER:
            self.assertTrue(expiry.upcoming(key, date(2026, 11, 1), 1, DATA) or key in ("natgas", "natgasm"), key)

    def test_nothing_in_the_past_and_today_counts(self):
        self.assertEqual(self.rows("nifty", date(2026, 11, 3))[0]["date"], date(2026, 11, 3))
        self.assertTrue(all(r["date"] >= date(2026, 11, 4) for r in self.rows("nifty", date(2026, 11, 4))))

    def test_years_without_holiday_data_are_marked_estimates(self):
        rows = expiry.upcoming("banknifty", date(2026, 12, 30), 2, DATA)
        self.assertTrue(all(r["est"] for r in rows))
        self.assertFalse(self.rows("banknifty")[0]["est"])


class McxRules(unittest.TestCase):
    def test_listed_date_wins_and_is_not_an_estimate(self):
        rows = [r for r in expiry.upcoming("crude", date(2026, 11, 1), 9, DATA) if r["kind"] == "Futures"]
        self.assertEqual((rows[0]["date"], rows[0]["est"]), (date(2026, 11, 19), False))
        self.assertEqual(len([r for r in rows if abs((r["date"] - date(2026, 11, 19)).days) <= 4]), 1)

    def test_estimates_follow_the_pattern_and_skip_weekends_and_holidays(self):
        gold = expiry.upcoming("gold", date(2026, 11, 20), 2, DATA)
        self.assertEqual(gold[0]["date"], date(2026, 12, 4))      # 5th is a Saturday
        self.assertTrue(gold[0]["est"])
        silver_mini = expiry.upcoming("silverm", date(2026, 12, 1), 1, DATA)
        self.assertEqual(silver_mini[0]["date"], date(2026, 12, 31))

    def test_product_without_a_pattern_says_so(self):
        self.assertEqual(expiry.upcoming("natgas", date(2026, 11, 1), 3, DATA), [])
        card = expiry.expiry_card(["natgas", "natgasm"], date(2026, 11, 1), data=DATA)
        self.assertIn("Not loaded yet", card)
        self.assertIn("Nat Gas", card)

    def test_missing_or_broken_data_file_does_not_crash(self):
        self.assertTrue(expiry.upcoming("nifty", date(2026, 11, 1), 2, {}))
        self.assertEqual(expiry.load_data("/nonexistent/file.json"), {})

    def test_shipped_data_file_is_valid(self):
        data = expiry.load_data()
        for key in ("nse_holidays", "mcx_holidays"):
            self.assertTrue(expiry._dates(data[key]))
            self.assertEqual(len(expiry._dates(data[key])), len(data[key]))
        self.assertTrue(data["expiries"])
        for row in data["expiries"]:
            self.assertIn(row["i"], expiry.INSTRUMENTS)
            date.fromisoformat(row["d"])


class FromTheExchangeSchedule(unittest.TestCase):
    """Nov 2026 dates read from the MCXCCL delivery schedule the user supplied."""
    def test_listed_november_dates(self):
        data = expiry.load_data()
        today = date(2026, 11, 6)
        for key in ("silverm",):
            row = expiry.next_expiries(key, today, data)["Futures"]
            self.assertEqual((row["date"], row["est"]), (date(2026, 11, 30), False), key)

    def test_silver_mini_is_month_end_not_the_5th(self):
        row = expiry.upcoming("silverm", date(2026, 12, 1), 1, DATA)[0]
        self.assertEqual(row["date"], date(2026, 12, 31))

    def test_big_gold_and_silver_only_in_their_contract_months(self):
        gold = [r["date"].month for r in expiry.upcoming("gold", date(2026, 11, 1), 4, DATA)]
        self.assertEqual(gold, [12, 2, 4, 6])
        silver = [r["date"].month for r in expiry.upcoming("silver", date(2026, 11, 1), 3, DATA)]
        self.assertEqual(silver, [12, 3, 5])


class Layout(unittest.TestCase):
    def test_each_instrument_is_a_block_with_exchange_and_arrow_lines(self):
        card = expiry.expiry_card(["nifty", "banknifty"], date(2026, 11, 11), data=DATA)
        self.assertIn("<b>📊 Indices · NSE</b>", card)
        self.assertIn("📈 <b>Nifty 50</b>\n    ↳ Weekly  <b>Tue 17 Nov</b> (in 6 days)\n"
                      "    ↳ Monthly  <b>Mon 23 Nov</b> (in 12 days)", card)
        self.assertIn("🏦 <b>Bank Nifty</b>\n    ↳ Monthly", card)
        self.assertLess(card.index("Nifty 50"), card.index("Bank Nifty"))

    def test_minis_exist_and_follow_their_parents(self):
        for key in ("goldm", "silverm", "crudem"):
            self.assertIn(key, expiry.INSTRUMENTS)
        self.assertEqual(expiry.find_instruments("crude mini silver micro gold mini"),
                         ["goldm", "silverm", "crudem"])
        self.assertEqual(expiry.find_instruments("crude")[0], "crude")
        mini = expiry.next_expiries("crudem", date(2026, 11, 1), DATA)["Futures"]
        self.assertEqual((mini["date"], mini["est"]), (date(2026, 11, 19), True))   # same day as crude

    def test_commodities_show_options_and_futures_with_mcx_tag(self):
        card = expiry.expiry_card(["crude", "gold"], date(2026, 11, 11))
        self.assertIn("<b>🪙 Commodities · MCX</b>", card)
        self.assertIn("🛢️ <b>Crude</b>\n    ↳ Options  <b>Tue 17 Nov</b>", card)
        self.assertIn("    ↳ Futures  <b>Thu 19 Nov</b>", card)
        self.assertIn("    ↳ Options  <b>Fri 27 Nov</b>", card)
        self.assertEqual(expiry.next_expiries("natgas", date(2026, 11, 11))["Options"]["date"],
                         date(2026, 11, 20))

    def test_three_sets_in_order_each_instrument_once(self):
        card = expiry.expiry_card(expiry.ORDER, date(2026, 11, 11))
        heads = ["📊 Indices · NSE", "🪙 Commodities · NSE", "🪙 Commodities · MCX"]
        self.assertEqual([card.index(h) for h in heads], sorted(card.index(h) for h in heads))
        indices, nse, mcx = (card.split("<blockquote>")[i] for i in (1, 2, 3))
        self.assertNotIn("Crude", indices)
        self.assertNotIn("Nifty", nse + mcx)
        self.assertEqual(card.count("↳ Weekly"), 1)
        self.assertIn("Options", nse)
        self.assertIn("Options", mcx)
        headers = [ln for ln in card.splitlines() if re.fullmatch(r"\S+ <b>[^<]+</b>", ln)]
        self.assertEqual(len(headers), len(expiry.ORDER))     # one block per instrument
        self.assertLess(len(card), 3500)

    def test_blocks_are_sorted_soonest_first_and_title_has_the_date(self):
        card = expiry.expiry_card(["gold", "crude", "natgas"], date(2026, 11, 11))
        self.assertIn("<i>Wed 11 Nov</i>", card.splitlines()[0])
        self.assertLess(card.index("Crude"), card.index("Nat Gas"))
        self.assertLess(card.index("Nat Gas"), card.index("Gold"))

    def test_nearest_dates_are_called_out_in_bold(self):
        self.assertIn("(<b>today</b>)", expiry.expiry_card(["crude"], date(2026, 11, 19)))
        self.assertIn("(<b>tomorrow</b>)", expiry.expiry_card(["crude"], date(2026, 11, 18)))
        self.assertIn("(in 6 days)", expiry.expiry_card(["crude"], date(2026, 11, 11)))

    def test_only_the_requested_commodities_exist(self):
        mcx = [k for k, v in expiry.INSTRUMENTS.items() if v[3] == "MCX"]
        self.assertEqual(mcx, ["gold", "goldm", "silver", "silverm", "crude", "crudem", "natgas"])
        self.assertEqual(expiry.NSE_COMMODITY, ("nsecrude", "nsenatgas"))

    def test_nse_commodities_use_nse_holidays_and_are_estimates(self):
        today = date(2026, 11, 1)
        first = lambda key, data=DATA: expiry.upcoming(key, today, 1, data)[0]
        self.assertEqual(first("nsecrude")["date"], date(2026, 11, 19))
        self.assertEqual(first("nsenatgas")["date"], date(2026, 11, 25))
        self.assertTrue(all(first(k)["est"] for k in expiry.NSE_COMMODITY))
        data = dict(DATA, nse_holidays=["2026-11-19"], mcx_holidays=[])
        self.assertEqual(first("nsecrude", data)["date"], date(2026, 11, 18))

    def test_typed_nse_names_do_not_swallow_mcx_ones(self):
        self.assertEqual(expiry.find_instruments("nse crude, crude mini, nse nat gas"),
                         ["nsecrude", "nsenatgas", "crudem"])
        for key in expiry.NSE_COMMODITY:
            self.assertEqual(commands.parse(expiry.label(key)), ("/xtoggle", [key]))

    def test_picker_rows_stay_inside_one_group(self):
        rows = [[b["text"] for b in r] for r in commands.expiry_keyboard(["gold"])["keyboard"]]
        self.assertLessEqual(max(len(r) for r in rows), 3)
        self.assertIn("🥇 Gold ✓", [t for r in rows for t in r])
        for r in rows[:-1]:
            groups = {"NSE" if ("Nifty" in t or t.startswith(("🌱", "🚀", "🧾"))) else "X" for t in r}
            self.assertEqual(len(groups), 1, r)


class ListedCalendar(unittest.TestCase):
    """Exact dates copied from the Groww commodities calendar (Nov 2026 to Jan 2027)."""
    def get(self, key, kind, today=date(2026, 11, 11)):
        rows = [r for r in expiry.upcoming(key, today, 40) if expiry.category(r["kind"]) == kind]
        return [(r["date"].isoformat(), r["est"]) for r in rows][:3]

    def test_mcx_futures(self):
        self.assertEqual(self.get("crude", "Futures"), [("2026-11-19", False), ("2026-12-18", False), ("2027-01-19", False)])
        self.assertEqual(self.get("crudem", "Futures")[:3], self.get("crude", "Futures"))
        self.assertEqual(self.get("natgas", "Futures"), [("2026-11-24", False), ("2026-12-28", False), ("2027-01-25", False)])
        self.assertEqual(self.get("goldm", "Futures")[:2], [("2026-12-04", False), ("2027-01-05", False)])
        self.assertEqual(self.get("gold", "Futures")[0], ("2026-12-04", False))
        self.assertEqual(self.get("silver", "Futures")[0], ("2026-12-04", False))

    def test_silver_mini_futures_after_november_are_estimates(self):
        self.assertEqual(self.get("silverm", "Futures"), [("2026-11-30", False), ("2026-12-31", True), ("2027-01-29", True)])

    def test_mcx_options(self):
        self.assertEqual(self.get("crude", "Options"), [("2026-11-17", False), ("2026-12-16", False), ("2027-01-15", False)])
        self.assertEqual(self.get("natgas", "Options"), [("2026-11-20", False), ("2026-12-23", False), ("2027-01-21", False)])
        self.assertEqual(self.get("gold", "Options"), [("2026-11-27", False), ("2026-12-31", False), ("2027-01-29", False)])
        self.assertEqual(self.get("goldm", "Options"), [("2026-11-27", False), ("2026-12-29", False), ("2027-01-29", False)])
        self.assertEqual(self.get("silver", "Options"), [("2026-11-27", False), ("2026-12-28", False), ("2027-01-25", False)])
        self.assertEqual(self.get("silverm", "Options"), [("2026-11-23", False), ("2026-12-28", False), ("2027-01-25", False)])

    def test_options_rule_reproduces_every_listed_crude_and_natgas_date(self):
        data = expiry.load_data()
        bare = dict(data, expiries=[r for r in data["expiries"] if r["t"] == "Futures"])
        for key in ("crude", "crudem", "natgas"):
            listed = [(r["d"]) for r in data["expiries"] if r["i"] == key and r["t"] == "Options"]
            guessed = [r["date"].isoformat() for r in expiry.upcoming(key, date(2026, 11, 11), 40, bare)
                       if r["kind"] == "Options"][:3]
            self.assertEqual(guessed, listed, key)

    def test_options_estimates_continue_past_the_listed_months_and_are_marked(self):
        rows = [r for r in expiry.upcoming("crude", date(2027, 1, 20), 20) if r["kind"] == "Options"]
        self.assertTrue(rows)
        self.assertTrue(all(r["est"] for r in rows))
        self.assertEqual(rows[0]["date"], date(2027, 2, 17))             # 19 Feb futures, 2 trading days earlier
        self.assertEqual([r for r in expiry.upcoming("gold", date(2027, 2, 1), 20) if r["kind"] == "Options"], [])

    def test_nse_commodities(self):
        self.assertEqual(self.get("nsecrude", "Futures"), [("2026-11-19", False), ("2026-12-18", False), ("2027-01-19", False)])
        self.assertEqual(self.get("nsenatgas", "Futures"), [("2026-11-24", False), ("2026-12-28", False), ("2027-01-25", False)])
        self.assertEqual(self.get("nsecrude", "Options"), [("2026-12-09", False), ("2027-01-08", False)])
        self.assertEqual(self.get("nsenatgas", "Options"), [("2026-11-18", False), ("2026-12-21", False), ("2027-01-19", False)])

    def test_nse_has_no_gold_or_silver_here(self):
        for gone in ("nsegold", "nsegoldm", "natgasm", "copper", "zinc"):
            self.assertNotIn(gone, expiry.INSTRUMENTS)

    def test_republic_day_2027_moves_the_january_monthly(self):
        row = [r for r in expiry.upcoming("banknifty", date(2027, 1, 1), 1)][0]
        self.assertEqual((row["date"], row["est"]), (date(2027, 1, 25), True))   # 2027 holidays not all known


class Words(unittest.TestCase):
    def test_find_instruments(self):
        self.assertEqual(expiry.find_instruments("when is bank nifty expiry"), ["banknifty"])
        self.assertEqual(expiry.find_instruments("nifty and gold, crude oil"), ["nifty", "gold", "crude"])
        self.assertEqual(expiry.find_instruments("nat gas & natural gas"), ["natgas"])
        self.assertEqual(expiry.find_instruments("hello"), [])

    def test_card_is_valid_html_short_and_flags_estimates(self):
        card = expiry.expiry_card(expiry.ORDER, date(2026, 11, 1), count=3, data=DATA)
        self.assertLess(len(card), 4000)
        for tag in ("b", "i"):
            self.assertEqual(card.count(f"<{tag}>"), card.count(f"</{tag}>"))
        self.assertIn("Confirm with the exchange", card)
        self.assertNotIn("~", expiry.expiry_card(["nifty"], date(2026, 11, 1), data=DATA))


class Bot(unittest.TestCase):
    def setUp(self):
        self.store = SubscriberStore("x", KEY, 10)
        self.aud = Audience(["100"], self.store, default_level="medium")
        self.tap("/start")

    def tap(self, text, chat=7):
        return commands.respond(chat, text, self.aud)

    def names(self, kb):
        return [b["text"] for row in kb["keyboard"] for b in row]

    def test_dock_has_expiries_button_and_every_button_parses(self):
        self.assertIn(commands.BTN_EXPIRY, self.names(commands.main_keyboard("high")))
        for kb in (commands.main_keyboard("high"), commands.expiry_keyboard(["nifty", "gold"]),
                   commands.expiry_keyboard(None)):
            for text in self.names(kb):
                self.assertIsNotNone(commands.parse(text)[0], text)

    def test_picker_toggles_with_ticks_on_buttons_and_saves(self):
        reply, kb = self.tap(commands.BTN_EXPIRY)
        self.assertIn("Pick your expiries", reply)
        self.assertTrue(kb.get("is_persistent"))
        reply, kb = self.tap("📈 Nifty 50")
        self.assertIn("is in", reply)
        self.assertIn("📈 Nifty 50 ✓", self.names(kb))
        self.assertEqual(self.store.get("7")["exp"], ["nifty"])
        self.tap("🥇 Gold")
        self.tap("📈 Nifty 50 ✓")
        self.assertEqual(self.store.get("7")["exp"], ["gold"])
        self.assertNotIn("✓", self.tap(commands.BTN_EXPIRY)[0])
        self.tap("🥇 Gold")
        self.assertNotIn("exp", self.store.get("7"))

    def test_toggle_keeps_level_and_currencies(self):
        self.tap("/level medium")
        self.tap("🇺🇸 USD")
        self.tap("🥈 Silver")
        rec = self.store.get("7")
        self.assertEqual((rec["level"], rec["cur"], rec["exp"]), ("medium", ["USD"], ["silver"]))

    def test_ask_by_typing_and_slash_command(self):
        reply, kb = self.tap("when is bank nifty expiry?")
        self.assertIn("Bank Nifty", reply)
        self.assertNotIn("Gold", reply)
        self.assertIsNone(kb)                                    # the bar stays as it was
        self.assertIn("Gold", self.tap("/expiry gold crude")[0])
        self.assertIn("Silver Mini", self.tap("/expiry all")[0])
        self.assertIn("Silver Mini", self.tap(commands.BTN_EXPIRY_ALL)[0])
        self.assertIsNone(self.store.get("7").get("exp"))        # looking never saves a pick

    def test_status_card_lists_picks(self):
        self.tap("🏦 Bank Nifty")
        self.assertIn("🏦 Bank Nifty", self.tap("🧭 My setup")[0])
        self.assertIn("None picked", commands._card("high", None))

    def test_back_returns_to_main_buttons(self):
        self.tap(commands.BTN_EXPIRY)
        self.assertIn(commands.BTN_EXPIRY, self.names(self.tap("⬅️ Back")[1]))

    def test_owner_picking_an_expiry_keeps_the_owner_default_level(self):
        self.tap("🥇 Gold", chat=100)       # owner chat 100 never sent /start
        rec = self.store.get("100")
        self.assertEqual((rec["level"], rec["cur"]), ("medium", None))
        self.assertEqual(rec["exp"], ["gold"])

    def test_stranger_must_start_first(self):
        self.assertIn("/start", self.tap("📅 Expiries", chat=55)[0])
        self.assertIsNone(self.store.get("55"))

    def test_expiry_picks_survive_save_and_reload(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.enc")
            store = SubscriberStore(path, KEY, 5)
            store.update("1", level="low", expiry=["nifty", "gold"])
            store.mark_expiry_sent("1", "nifty|2026-11-17|eve")
            store.save()
            again = SubscriberStore.load(path, KEY, 5)
            self.assertEqual(again.get("1")["exp"], ["nifty", "gold"])
            self.assertEqual(again.get("1")["xs"], ["nifty|2026-11-17|eve"])
            again.prune_expiry_sent("2026-11-18")
            self.assertNotIn("xs", again.get("1"))


class FakeBot:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def send(self, message, chat_id=None, reply_markup=None):
        if self.fail:
            raise self.fail
        self.sent.append((chat_id, message))


class Reminders(unittest.TestCase):
    def setUp(self):
        self.store = SubscriberStore("x", KEY, 10)
        self.store.update("7", level="high", expiry=["nifty", "banknifty"])
        self.store.update("8", level="high")                       # picked nothing
        self.aud = Audience(["100"], self.store)
        self.bot = FakeBot()

    def run_at(self, when):
        return reminders.send_expiry_reminders(self.bot, self.aud, when)

    def test_one_alert_the_evening_before(self):
        self.assertEqual(self.run_at(utc(2026, 11, 22, 17, 5)), 1)       # day before Mon 23 Nov
        chat, text = self.bot.sent[0]
        self.assertEqual(chat, "7")
        self.assertIn("Expiry tomorrow", text)
        self.assertIn("Bank Nifty", text)
        self.assertIn("Mon 23 Nov", text)
        self.assertEqual(self.run_at(utc(2026, 11, 23, 9, 0)), 0)      # no second, same-day message
        self.assertEqual(len(self.bot.sent), 1)

    def test_each_reminder_is_sent_once(self):
        self.run_at(utc(2026, 11, 22, 17, 5))
        self.assertEqual(self.run_at(utc(2026, 11, 22, 17, 10)), 0)
        self.assertEqual(self.run_at(utc(2026, 11, 22, 23, 0)), 0)
        self.assertEqual(len(self.bot.sent), 1)

    def test_not_too_early_not_after_the_close_and_nothing_for_nonpickers(self):
        self.assertEqual(self.run_at(utc(2026, 11, 22, 12, 0)), 0)
        self.assertEqual(self.run_at(utc(2026, 11, 23, 7, 0)), 0)
        self.assertEqual(self.run_at(utc(2026, 11, 23, 16, 0)), 0)
        self.assertEqual(self.run_at(utc(2026, 11, 20, 17, 5)), 0)       # a quiet day
        self.assertEqual(self.bot.sent, [])

    def test_two_instruments_arrive_as_one_message(self):
        self.store.update("7", expiry=["nifty", "banknifty"])
        self.run_at(utc(2026, 12, 28, 18, 0))                            # eve of Tue 29 Dec
        self.assertEqual(len(self.bot.sent), 1)
        self.assertIn("Nifty 50", self.bot.sent[0][1])
        self.assertIn("Bank Nifty", self.bot.sent[0][1])

    def test_message_is_short_and_keeps_each_kind_apart(self):
        self.store.update("7", expiry=["nifty", "crude", "crudem"])
        self.run_at(utc(2026, 11, 18, 17, 30))
        text = self.bot.sent[0][1]
        self.assertIn("Thu 19 Nov", text)
        self.assertIn("🛢️ <b>Crude</b>\n    ↳ Futures\n", text)
        self.assertIn("🪙 Commodities · MCX", text)
        self.assertNotIn("Indices", text)
        self.assertNotIn("Weekly", text)
        self.assertNotIn("Options", text)
        self.assertNotIn("~", text)                         # both dates are listed
        self.assertNotIn("Confirm", text)                   # exact dates: no disclaimer needed
        self.store.update("7", expiry=["nifty"])
        self.run_at(utc(2026, 11, 16, 17, 30))
        text = self.bot.sent[1][1]
        self.assertIn("📊 Indices", text)
        self.assertIn("↳ Weekly", text)
        self.assertNotIn("Commodities", text)
        self.assertLess(len(text), 250)
        self.store.update("7", expiry=["gold"])
        self.run_at(utc(2026, 11, 26, 17, 30))              # eve of the Gold options expiry
        self.assertIn("↳ Options", self.bot.sent[2][1])

    def test_failed_send_is_retried_and_blocked_users_are_removed(self):
        self.bot.fail = RuntimeError("boom")
        self.assertEqual(self.run_at(utc(2026, 11, 22, 17, 5)), 0)
        self.bot.fail = None
        self.assertEqual(self.run_at(utc(2026, 11, 22, 17, 10)), 1)
        blocked = RuntimeError("blocked")
        blocked.code = 403
        self.bot = FakeBot(fail=blocked)
        self.assertEqual(self.run_at(utc(2026, 12, 28, 18, 0)), 0)
        self.assertIsNone(self.store.get("7"))

    def test_paused_owner_gets_nothing_and_no_store_means_no_reminders(self):
        self.store.update("100", level="off", expiry=["nifty"])
        self.run_at(utc(2026, 11, 22, 17, 5))
        self.assertNotIn("100", [c for c, _ in self.bot.sent])
        self.assertEqual(reminders.send_expiry_reminders(self.bot, Audience(["1"], None), utc(2026, 11, 22, 17, 5)), 0)


if __name__ == "__main__":
    unittest.main()
