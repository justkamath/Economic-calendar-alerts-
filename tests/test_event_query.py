"""Typed questions about calendar events ("high impact events next week").
Run: python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import commands  # noqa: E402
import config as cfg  # noqa: E402
import event_query as q  # noqa: E402
from subscribers import Audience, SubscriberStore  # noqa: E402

KEY = "a-long-random-secret-key-123456"
TODAY = date(2026, 11, 27)                                    # a Friday
NOW = datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc)       # 9:30 am IST


def ev(day, hh, title, country="USD", impact="high", **extra):
    """An event at IST clock time on `day`, as the calendar feed writes it."""
    when = datetime(day.year, day.month, day.day, hh, 0, tzinfo=cfg.DISPLAY_TZ)
    return dict({"title": title, "country": country, "impact": impact.capitalize(),
                 "date": when.astimezone(timezone.utc).isoformat()}, **extra)


CAL = [
    ev(date(2026, 11, 27), 18, "Core PCE m/m", "USD", "high", previous="0.2%", forecast="0.3%"),
    ev(date(2026, 11, 27), 19, "Chicago PMI", "USD", "medium"),
    ev(date(2026, 11, 30), 13, "CPI Flash y/y", "EUR", "high"),
    ev(date(2026, 12, 2), 9, "Retail Sales", "GBP", "low"),
    ev(date(2026, 12, 3), 18, "Non-Farm Payrolls", "USD", "high"),
    ev(date(2026, 12, 20), 10, "Far away", "JPY", "high"),
]


class Reading(unittest.TestCase):
    def ask(self, text):
        return q.parse_query(text, TODAY)

    def test_what_counts_as_an_event_question(self):
        for text in ("give me all high impact events for next 10 days", "events tomorrow",
                     "usd events next week", "what's on today", "all events 5 dec",
                     "calendar this week", "high impact news on friday"):
            self.assertTrue(q.looks_like_query(text), text)
        for text in ("high", "🔴 High ✓", "🎯 Sharp", "when is nifty expiry", "📅 Expiries",
                     "/level high", "USD", "🌍 All currencies", "pause"):
            self.assertFalse(q.looks_like_query(text), text)

    def test_next_n_days_and_weeks(self):
        a = self.ask("give me all high impact events for next 10 days")
        self.assertEqual((a["start"], a["end"]), (TODAY, date(2026, 12, 6)))
        self.assertEqual(a["levels"], ["high"])
        b = self.ask("events next 2 weeks")
        self.assertEqual((b["start"], b["end"]), (TODAY, date(2026, 12, 10)))
        c = self.ask("events next two weeks")
        self.assertEqual(c["end"], date(2026, 12, 10))

    def test_weeks_months_and_single_days(self):
        self.assertEqual(self.ask("events next week")["start"], date(2026, 11, 30))
        self.assertEqual(self.ask("events next week")["end"], date(2026, 12, 6))
        self.assertEqual(self.ask("events this week")["end"], date(2026, 11, 29))
        self.assertEqual(self.ask("events tomorrow")["start"], date(2026, 11, 28))
        self.assertEqual(self.ask("events today")["end"], TODAY)
        self.assertEqual(self.ask("events yesterday")["start"], date(2026, 11, 26))
        self.assertEqual(self.ask("events on monday")["start"], date(2026, 11, 30))
        self.assertEqual(self.ask("events on friday")["start"], TODAY)
        nxt = self.ask("events next month")
        self.assertEqual((nxt["start"], nxt["end"]), (date(2026, 12, 1), date(2026, 12, 31)))

    def test_written_dates_and_ranges(self):
        for text in ("events 5 dec", "events dec 5", "events 5th december", "events 2026-12-05",
                     "events 5/12"):
            self.assertEqual(self.ask(text)["start"], date(2026, 12, 5), text)
        r = self.ask("events from 2 dec to 5 dec")
        self.assertEqual((r["start"], r["end"]), (date(2026, 12, 2), date(2026, 12, 5)))
        self.assertEqual(self.ask("events 5 jan")["start"], date(2027, 1, 5))   # next year's date

    def test_specific_date_ranges_in_every_way_people_write_them(self):
        for text in ("Give me high impact events from 7-12 dec", "events 7 to 12 dec",
                     "events from 7th to 12th december", "events 7 - 12 dec", "events dec 7-12",
                     "events between 7 and 12 dec", "events 7 dec to 12 dec", "events 7 dec - 12 dec",
                     "events 7–12 Dec", "high impact events from 7 dec till 12 dec"):
            r = self.ask(text)
            self.assertEqual((r["start"], r["end"]), (date(2026, 12, 7), date(2026, 12, 12)), text)
        self.assertEqual(self.ask("high impact events from 7-12 dec")["levels"], ["high"])
        r = self.ask("events 28-3 jan")                                         # across a month end
        self.assertEqual((r["start"], r["end"]), (date(2026, 12, 28), date(2027, 1, 3)))
        r = self.ask("events 30 dec to 3 jan 2027")
        self.assertEqual((r["start"], r["end"]), (date(2026, 12, 30), date(2027, 1, 3)))
        r = self.ask("events 25 to 2 dec")
        self.assertEqual((r["start"], r["end"]), (date(2026, 11, 25), date(2026, 12, 2)))
        self.assertEqual(self.ask("events 5/12")["start"], date(2026, 12, 5))       # still day/month

    def test_no_time_means_the_next_seven_days(self):
        a = self.ask("all events")
        self.assertEqual((a["start"], a["end"]), (TODAY, date(2026, 12, 3)))
        self.assertTrue(a["everything"])

    def test_there_is_no_limit_on_how_far_a_question_can_reach(self):
        a = self.ask("events from 1 dec to 30 mar 2027")
        self.assertEqual((a["start"], a["end"]), (date(2026, 12, 1), date(2027, 3, 30)))
        self.assertEqual(self.ask("events next 200 days")["end"], TODAY + timedelta(days=199))

    def test_levels_and_currencies(self):
        a = self.ask("high and medium usd eur events")
        self.assertEqual(a["levels"], ["high", "medium"])
        self.assertEqual(a["currencies"], ["USD", "EUR"])
        self.assertTrue(self.ask("events for all currencies")["all_currencies"])


class Answering(unittest.TestCase):
    def reply(self, text, level="medium", cur=None, calendar=CAL, latest=date(2026, 12, 20)):
        out = q.build_reply(q.parse_query(text, TODAY), calendar, NOW, own_level=level,
                            own_cur=cur, latest=latest)
        return "\n\n".join(out)

    def test_high_impact_next_10_days_lists_only_high_grouped_by_day(self):
        text = self.reply("give me all high impact events for next 10 days")
        self.assertIn("High impact", text)
        for title in ("Core PCE m/m", "CPI Flash y/y", "Non-Farm Payrolls"):
            self.assertIn(title, text)
        for title in ("Chicago PMI", "Retail Sales", "Far away"):
            self.assertNotIn(title, text)
        self.assertLess(text.index("Fri 27 Nov"), text.index("Mon 30 Nov"))
        self.assertLess(text.index("Mon 30 Nov"), text.index("Thu 3 Dec"))
        self.assertIn("Previous <b>0.2%</b>", text)

    def test_the_question_wins_over_the_saved_setup(self):
        low = self.reply("low impact events 2 dec", level="high")
        self.assertIn("Retail Sales", low)                      # saved "Sharp" does not hide it
        self.assertNotIn("Retail Sales", self.reply("events 2 dec", level="high"))

    def test_without_a_level_the_saved_level_is_the_default(self):
        text = self.reply("events this week", level="high")
        self.assertIn("Core PCE", text)
        self.assertNotIn("Chicago PMI", text)
        text = self.reply("events this week", level="medium")
        self.assertIn("Chicago PMI", text)
        self.assertIn("Medium + high", text)

    def test_saved_currencies_apply_unless_the_question_says_otherwise(self):
        text = self.reply("high impact events next 10 days", cur=["EUR"])
        self.assertIn("CPI Flash y/y", text)
        self.assertNotIn("Core PCE", text)
        self.assertIn("your currencies", text)
        self.assertIn("Core PCE", self.reply("high impact events next 10 days usd", cur=["EUR"]))
        self.assertIn("Core PCE", self.reply("high impact events next 10 days all currencies",
                                             cur=["EUR"]))

    def test_nothing_found_and_beyond_the_calendar(self):
        text = self.reply("high impact events 28 dec", latest=date(2026, 12, 6))
        self.assertIn("Nothing on the list for those days yet", text)
        self.assertIn("after Sun 6 Dec aren't on the list yet", text)
        partly = self.reply("high impact events 3 dec to 10 dec", latest=date(2026, 12, 6))
        self.assertIn("Non-Farm Payrolls", partly)
        self.assertIn("after Sun 6 Dec aren't on the list yet", partly)
        self.assertIn("will be added soon", partly)
        none_high = self.reply("high impact events 2 dec", latest=date(2026, 12, 6))
        self.assertIn("Nothing matches", none_high)

    def test_long_answers_are_split_and_capped(self):
        many = [ev(date(2026, 12, 1), 1 + i % 20, f"Item {i} with a long name", "USD", "high",
                 previous="1.234%", forecast="1.345%") for i in range(400)]
        parts = q.build_reply(q.parse_query("all events 1 dec", TODAY), many, NOW, latest=None)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) < 4096 for p in parts))
        self.assertIn("+100 more", parts[-1])


class Bot(unittest.TestCase):
    def setUp(self):
        self.store = SubscriberStore("x", KEY, 10)
        self.store.update("7", level="high", currencies=["USD"], expiry=["gold"])
        self.aud = Audience(["100"], self.store)
        self.seen = []

    def provider(self, start, end):
        self.seen.append((start, end))
        return CAL

    def tap(self, text, events="use"):
        events = self.provider if events == "use" else events
        return commands.respond("7", text, self.aud, events)

    def test_typed_question_is_answered_and_never_changes_the_setup(self):
        before = dict(self.store.get("7"))
        reply, kb = self.tap("give me all high impact events for next 10 days")
        text = "\n\n".join(reply) if isinstance(reply, list) else reply
        self.assertIn("High impact", text)
        self.assertIsNone(kb)                                   # the buttons stay as they were
        self.assertEqual(self.store.get("7"), before)
        self.assertFalse(self.store.dirty and self.store.get("7") != before)

    def test_the_word_high_alone_still_sets_the_level(self):
        self.tap("medium")
        self.assertEqual(self.store.get("7")["level"], "medium")

    def test_slash_command_and_aliases(self):
        for text in ("/events usd tomorrow", "/calendar next week", "/events"):
            reply, _ = self.tap(text)
            self.assertTrue(reply)
        self.assertEqual(self.store.get("7")["cur"], ["USD"])

    def test_calendar_down_is_said_plainly(self):
        reply, _ = self.tap("events tomorrow", events=None)
        self.assertIn("isn't available", reply)

        def broken(start, end):
            raise RuntimeError("db")
        reply, _ = self.tap("events tomorrow", events=broken)
        self.assertIn("couldn't read", reply)

    def test_someone_not_started_is_told_to_start(self):
        reply, _ = commands.respond("999", "events tomorrow", self.aud, self.provider)
        self.assertIn("Start", reply)

    def test_long_answers_go_out_as_several_messages(self):
        many = [ev(date(2026, 12, 1), 1 + i % 20, f"Item {i} with a long name", "USD", "high",
                 previous="1.234%", forecast="1.345%") for i in range(60)]
        sent = []

        class Bot2:
            def get_updates(self, offset=0, limit=100):
                return [{"update_id": offset + 1, "message": {
                    "chat": {"id": 7, "type": "private"}, "text": "all events 1 dec"}}] if offset == 0 else []

            def send(self, message, chat_id=None, reply_markup=None):
                sent.append((message, reply_markup))

        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            commands.handle_updates(Bot2(), self.aud, lambda s, e: many)
        self.assertGreater(len(sent), 1)
        self.assertTrue(all(m is None or k is None for m, k in sent))


if __name__ == "__main__":
    unittest.main()
