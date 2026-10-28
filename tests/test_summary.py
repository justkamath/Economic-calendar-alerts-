"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
import summary  # noqa: E402

# 08:30 IST on Sunday 25 Oct 2026
NOW = datetime(2026, 10, 25, 3, 0, tzinfo=cfg.UTC)


def ev(hours, impact="High", title="CPI", country="USD", **extra):
    when = NOW + timedelta(hours=hours)
    return {"title": title, "country": country, "impact": impact,
            "date": when.isoformat(), **extra}


def whole(messages):
    """All messages of one summary as a single string."""
    return "\n".join(messages)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = (cfg.STATE_FILE, cfg.SEND_DELAY_SECONDS, cfg.SUMMARY_ENABLED,
                     cfg.SUMMARY_HOUR, cfg.SUMMARY_MIN_IMPACT)
        cfg.STATE_FILE = os.path.join(self._tmp.name, "state.json")
        cfg.SEND_DELAY_SECONDS = 0
        cfg.SUMMARY_ENABLED, cfg.SUMMARY_HOUR, cfg.SUMMARY_MIN_IMPACT = True, 8, "medium"

    def tearDown(self):
        (cfg.STATE_FILE, cfg.SEND_DELAY_SECONDS, cfg.SUMMARY_ENABLED,
         cfg.SUMMARY_HOUR, cfg.SUMMARY_MIN_IMPACT) = self._old
        self._tmp.cleanup()

    # ----- selecting events -----
    def test_only_today_and_min_impact(self):
        cal = [ev(5, "Medium", "M"), ev(7, "High", "H"), ev(1, "Low", "L"),
               ev(40, "High", "Tomorrow")]
        found = summary.todays_events(cal, NOW)
        self.assertEqual([e["title"] for _, _, e in found], ["H", "M"])

    def test_all_includes_every_level_and_unrated(self):
        cfg.SUMMARY_MIN_IMPACT = "all"
        cal = [ev(1, "Low", "L"), ev(2, "Holiday", "Hol"), ev(3, "", "NoLabel"),
               ev(4, "Non-Economic", "NonEco"), ev(5, "High", "H"),
               ev(6, "Medium", "M")]
        found = summary.todays_events(cal, NOW)
        self.assertEqual(len(found), 6)
        text = whole(summary.build_messages(found, NOW))
        self.assertIn("⚫ 2 Unrated", text)
        for title in ("L", "Hol", "NoLabel", "NonEco", "H", "M"):
            self.assertIn(f"<b>{title}</b>", text)

    # ----- the planner layout -----
    def test_header_counts_and_focus(self):
        cal = [ev(7, "High", "A", country="AUD"), ev(8, "High", "B", country="AUD"),
               ev(9, "Medium", "C", country="GBP")]
        text = whole(summary.build_messages(summary.todays_events(cal, NOW), NOW))
        self.assertIn("Sunday, 25 Oct 2026", text)
        self.assertIn("Good morning! Here's your day (IST)", text)
        self.assertIn("🔴 2 High   🟠 1 Medium", text)
        self.assertIn("🌍 In focus: 🇦🇺 AUD  🇬🇧 GBP", text)  # most High events first

    def test_events_in_time_order_grouped_by_part_of_day(self):
        cfg.SUMMARY_MIN_IMPACT = "all"
        cal = [ev(13, "Low", "Nightly"),       # 21:30 IST  -> NIGHT
               ev(10, "High", "Evening"),      # 18:30 IST  -> EVENING
               ev(5, "Medium", "Afternoon"),   # 13:30 IST  -> AFTERNOON
               ev(1, "High", "Morning"),       # 09:30 IST  -> MORNING
               ev(-6, "Low", "Early")]         # 02:30 IST  -> EARLY HOURS
        text = whole(summary.build_messages(summary.todays_events(cal, NOW), NOW))
        order = [text.index(x) for x in ("EARLY HOURS", "Early", "MORNING", "Morning",
                                         "AFTERNOON", "Afternoon", "EVENING",
                                         "Evening", "NIGHT", "Nightly")]
        self.assertEqual(order, sorted(order))
        self.assertIn("🌙 <b>EARLY HOURS</b>", text)
        self.assertIn("🌃 <b>NIGHT</b>", text)

    def test_empty_parts_of_the_day_are_skipped(self):
        text = whole(summary.build_messages(
            summary.todays_events([ev(1, "High", "Only")], NOW), NOW))
        self.assertIn("MORNING", text)
        for name in ("EARLY HOURS", "AFTERNOON", "EVENING", "NIGHT"):
            self.assertNotIn(name, text)

    def test_event_block_layout_and_escaping(self):
        cal = [ev(1, "High", "A <b> & B", country="USD", previous="1%", forecast="2%")]
        text = whole(summary.build_messages(summary.todays_events(cal, NOW), NOW))
        self.assertIn("🔴 <b>9:30 AM</b>  🇺🇸 USD\n    <b>A &lt;b&gt; &amp; B</b>\n"
                      "    ↳ Previous <b>1%</b>  |  Forecast <b>2%</b>", text)

    def test_numbers_only_for_high_and_medium(self):
        cfg.SUMMARY_MIN_IMPACT = "all"
        cal = [ev(1, "High", "H", previous="1%"), ev(2, "Medium", "M", previous="2%"),
               ev(3, "Low", "L", previous="3%")]
        text = whole(summary.build_messages(summary.todays_events(cal, NOW), NOW))
        self.assertIn("Previous <b>1%</b>", text)
        self.assertIn("Previous <b>2%</b>", text)
        self.assertNotIn("Previous <b>3%</b>", text)

    def test_blank_line_after_every_event_and_no_double_blanks(self):
        cal = [ev(1, "High", "A", previous="1%"), ev(2, "High", "B")]
        text = whole(summary.build_messages(
            summary.todays_events(cal, NOW), NOW, []))
        self.assertIn("Previous <b>1%</b>\n\n🔴", text)   # blank between events
        self.assertNotIn("\n\n\n", text)
        self.assertFalse(text.endswith("\n"))

    def test_unknown_country_gets_globe_flag(self):
        found = summary.todays_events([ev(2, country="XYZ")], NOW)
        self.assertIn("🌐 XYZ", whole(summary.build_messages(found, NOW)))

    def test_empty_day_message(self):
        text = summary.build_messages([], NOW)[0]
        self.assertIn("quiet day", text)

    def test_tomorrow_preview(self):
        found = summary.todays_events([ev(2)], NOW)
        tom = summary.events_for_day(
            [ev(30, "High", "T1"), ev(31, "Medium", "T2")],
            (NOW + timedelta(days=1)).astimezone(cfg.DISPLAY_TZ).date())
        text = whole(summary.build_messages(found, NOW, tom))
        self.assertIn("Tomorrow:</b> 2 events", text)
        self.assertIn("nothing scheduled",
                      whole(summary.build_messages(found, NOW, [])))
        self.assertIn("nothing scheduled", summary.build_messages([], NOW, [])[0])

    def test_one_message_normally_and_split_when_huge(self):
        one = summary.build_messages(summary.todays_events([ev(1), ev(2)], NOW), NOW)
        self.assertEqual(len(one), 1)
        cal = [ev(1, "Medium", "Event number %d " % i + "x" * 60) for i in range(120)]
        msgs = summary.build_messages(summary.todays_events(cal, NOW), NOW)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= 4096 for m in msgs))

    # ----- sending -----
    def test_sent_once_per_day(self):
        n, st = FakeNotifier(), {}
        cal = [ev(2), ev(26)]   # one High event today, one tomorrow
        summary.maybe_send_summary(cal, NOW, n, st)
        per_day = len(n.sent)
        self.assertGreaterEqual(per_day, 1)
        summary.maybe_send_summary(cal, NOW + timedelta(minutes=5), n, st)
        self.assertEqual(len(n.sent), per_day)          # not sent twice
        summary.maybe_send_summary(cal, NOW + timedelta(days=1), n, st)
        self.assertEqual(len(n.sent), 2 * per_day)      # sent again next day

    def test_not_before_summary_hour(self):
        n, st = FakeNotifier(), {}
        summary.maybe_send_summary([ev(2)], NOW - timedelta(hours=2), n, st)
        self.assertEqual(n.sent, [])

    def test_resend_forces_summary_even_if_sent_or_early(self):
        n, st = FakeNotifier(), {}
        early = NOW - timedelta(hours=2)  # 06:30 IST, before SUMMARY_HOUR
        cfg.RESEND_SUMMARY = True
        try:
            summary.maybe_send_summary([ev(2)], early, n, st)   # early: still sends
            first = len(n.sent)
            summary.maybe_send_summary([ev(2)], early, n, st)   # already sent: again
        finally:
            cfg.RESEND_SUMMARY = False
        self.assertGreaterEqual(first, 1)
        self.assertEqual(len(n.sent), 2 * first)

    def test_resend_does_not_override_disabled(self):
        cfg.SUMMARY_ENABLED = False
        cfg.RESEND_SUMMARY = True
        n = FakeNotifier()
        try:
            summary.maybe_send_summary([ev(2)], NOW, n, {})
        finally:
            cfg.RESEND_SUMMARY = False
        self.assertEqual(n.sent, [])

    def test_disabled(self):
        cfg.SUMMARY_ENABLED = False
        n = FakeNotifier()
        summary.maybe_send_summary([ev(2)], NOW, n, {})
        self.assertEqual(n.sent, [])

    def test_failed_send_is_retried(self):
        class Failing:
            def send(self, m):
                raise RuntimeError("boom")
        st = {}
        summary.maybe_send_summary([ev(2)], NOW, Failing(), st)
        n = FakeNotifier()
        summary.maybe_send_summary([ev(2)], NOW, n, st)
        self.assertEqual(len(n.sent), len(summary.build_messages(
            summary.todays_events([ev(2)], NOW), NOW, [])))

    def test_failure_midway_resumes_without_repeating(self):
        cal = [ev(1, "Medium", "Event %d " % i + "x" * 80) for i in range(80)]
        total = len(summary.build_messages(summary.todays_events(cal, NOW), NOW, []))
        self.assertGreaterEqual(total, 3)

        class FlakyOnce:
            def __init__(self):
                self.sent, self.failed = [], False

            def send(self, message):
                if len(self.sent) == 2 and not self.failed:
                    self.failed = True
                    raise RuntimeError("network")
                self.sent.append(message)

        n, st = FlakyOnce(), {}
        summary.maybe_send_summary(cal, NOW, n, st)       # fails after 2 messages
        self.assertEqual(len(n.sent), 2)
        self.assertFalse(st[summary.summary_key(NOW)]["sent"])
        summary.maybe_send_summary(cal, NOW, n, st)       # resumes at message 3
        self.assertEqual(len(n.sent), total)
        self.assertEqual(len(set(n.sent)), total)         # nothing sent twice
        self.assertTrue(st[summary.summary_key(NOW)]["sent"])

    def test_marker_is_pruned_later(self):
        import state as state_mod
        st = {}
        summary.maybe_send_summary([ev(2)], NOW, FakeNotifier(), st)
        state_mod.prune_state(st, NOW + timedelta(days=10))
        self.assertEqual(st, {})


if __name__ == "__main__":
    unittest.main()
