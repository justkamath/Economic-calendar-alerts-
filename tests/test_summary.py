"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
import summary  # noqa: E402

# 08:30 IST on 25 Oct 2026
NOW = datetime(2026, 10, 25, 3, 0, tzinfo=cfg.UTC)


def ev(hours, impact="High", title="CPI", country="USD", **extra):
    when = NOW + timedelta(hours=hours)
    return {"title": title, "country": country, "impact": impact,
            "date": when.isoformat(), **extra}


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

    def test_only_today_and_min_impact_sorted_by_importance(self):
        cal = [ev(5, "Medium", "M"), ev(7, "High", "H"), ev(1, "Low", "L"),
               ev(40, "High", "Tomorrow")]
        found = summary.todays_events(cal, NOW)
        self.assertEqual([e["title"] for _, _, e in found], ["H", "M"])

    def test_all_includes_every_level_and_na(self):
        cfg.SUMMARY_MIN_IMPACT = "all"
        cal = [ev(1, "Low", "L"), ev(2, "Holiday", "Hol"), ev(3, "", "NoLabel"),
               ev(4, "Non-Economic", "NonEco"), ev(5, "High", "H"),
               ev(6, "Medium", "M")]
        found = summary.todays_events(cal, NOW)
        self.assertEqual([e["title"] for _, _, e in found],
                         ["H", "M", "L", "Hol", "NoLabel", "NonEco"])
        text = "\n".join(summary.build_messages(found, NOW))
        self.assertIn("⚫ <b>UNRATED</b>", text)
        self.assertIn("⚫ 2 Unrated", text)

    def test_empty_day_all_message(self):
        cfg.SUMMARY_MIN_IMPACT = "all"
        self.assertIn("quiet day", summary.build_messages([], NOW)[0])

    def test_message_content(self):
        cal = [ev(7, "High", "A <b> & B", previous="1%", forecast="2%")]
        text = summary.build_messages(summary.todays_events(cal, NOW), NOW)[0]
        self.assertIn("Sunday, 25 Oct 2026", text)
        self.assertIn("Good morning", text)
        self.assertIn("🔴 <b>HIGH IMPACT</b>", text)
        self.assertIn("🇺🇸 USD", text)
        self.assertIn("A &lt;b&gt; &amp; B", text)
        self.assertIn("Previous <b>1%</b>", text)
        self.assertIn("Forecast <b>2%</b>", text)
        self.assertIn("🔴 1 High", text)

    def test_empty_day_message(self):
        text = summary.build_messages([], NOW)[0]
        self.assertIn("quiet day", text)

    def test_tomorrow_preview(self):
        found = summary.todays_events([ev(2)], NOW)
        tom = summary.events_for_day(
            [ev(30, "High", "T1"), ev(31, "Medium", "T2")],
            (NOW + timedelta(days=1)).astimezone(cfg.DISPLAY_TZ).date())
        text = summary.build_messages(found, NOW, tom)[0]
        self.assertIn("Tomorrow:</b> 2 events", text)
        self.assertIn("nothing scheduled",
                      summary.build_messages(found, NOW, [])[0])

    def test_same_time_events_share_one_time_heading(self):
        cal = [ev(2, "High", "A", country="AUD"), ev(2, "High", "B", country="AUD"),
               ev(4, "High", "C", country="USD")]
        text = summary.build_messages(summary.todays_events(cal, NOW), NOW)[0]
        self.assertEqual(text.count("🕒"), 2)   # two distinct times, not three
        self.assertIn("➤ 🇦🇺 AUD — <b>A</b>", text)
        self.assertIn("➤ 🇦🇺 AUD — <b>B</b>", text)

    def test_blank_line_after_each_event_and_no_double_blanks(self):
        cal = [ev(2, "High", "A", country="AUD", previous="1%"),
               ev(4, "High", "B", country="USD"), ev(5, "Low", "C")]
        text = summary.build_messages(summary.todays_events(cal, NOW), NOW, [])[0]
        self.assertIn("↳ Previous <b>1%</b>\n\n", text)   # blank after event A
        self.assertIn("<b>B</b>\n\n", text)                # and after event B
        self.assertNotIn("\n\n\n", text)
        self.assertFalse(text.endswith("\n"))

    def test_singular_plural_event_count(self):
        one = summary.todays_events([ev(2)], NOW)
        self.assertIn("(1 event)", summary.build_messages(one, NOW)[0])
        two = summary.todays_events([ev(2), ev(3)], NOW)
        self.assertIn("(2 events)", summary.build_messages(two, NOW)[0])

    def test_unknown_country_gets_globe_flag(self):
        found = summary.todays_events([ev(2, country="XYZ")], NOW)
        self.assertIn("🌐 XYZ", summary.build_messages(found, NOW)[0])

    def test_long_summary_is_split(self):
        cal = [ev(1, "Medium", "Event number %d " % i + "x" * 60) for i in range(120)]
        msgs = summary.build_messages(summary.todays_events(cal, NOW), NOW)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= 4096 for m in msgs))

    def test_sent_once_per_day(self):
        n, st = FakeNotifier(), {}
        summary.maybe_send_summary([ev(2)], NOW, n, st)
        summary.maybe_send_summary([ev(2)], NOW + timedelta(minutes=5), n, st)
        self.assertEqual(len(n.sent), 1)
        summary.maybe_send_summary([ev(2)], NOW + timedelta(days=1), n, st)
        self.assertEqual(len(n.sent), 2)

    def test_not_before_summary_hour(self):
        n, st = FakeNotifier(), {}
        early = NOW - timedelta(hours=2)  # 06:30 IST
        summary.maybe_send_summary([ev(2)], early, n, st)
        self.assertEqual(n.sent, [])

    def test_resend_forces_summary_even_if_sent_or_early(self):
        n, st = FakeNotifier(), {}
        early = NOW - timedelta(hours=2)  # 06:30 IST, before SUMMARY_HOUR
        cfg.RESEND_SUMMARY = True
        try:
            summary.maybe_send_summary([ev(2)], early, n, st)   # early: still sends
            summary.maybe_send_summary([ev(2)], early, n, st)   # already sent: again
        finally:
            cfg.RESEND_SUMMARY = False
        self.assertEqual(len(n.sent), 2)

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
        self.assertEqual(len(n.sent), 1)

    def test_marker_is_pruned_later(self):
        import state as state_mod
        st = {}
        summary.maybe_send_summary([ev(2)], NOW, FakeNotifier(), st)
        state_mod.prune_state(st, NOW + timedelta(days=10))
        self.assertEqual(st, {})


if __name__ == "__main__":
    unittest.main()
