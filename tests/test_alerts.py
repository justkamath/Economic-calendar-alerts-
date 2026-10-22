"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
import alerts  # noqa: E402
import state as state_mod  # noqa: E402
from utils import clean, parse_event_time  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=cfg.UTC)


def make_event(minutes, impact="High", title="FOMC", country="USD"):
    when = NOW + timedelta(minutes=minutes)
    return {"title": title, "country": country, "impact": impact,
            "date": when.isoformat()}


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class AlertTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = (cfg.STATE_FILE, cfg.SEND_DELAY_SECONDS)
        cfg.STATE_FILE = os.path.join(self._tmp.name, "state.json")
        cfg.SEND_DELAY_SECONDS = 0

    def tearDown(self):
        cfg.STATE_FILE, cfg.SEND_DELAY_SECONDS = self._old
        self._tmp.cleanup()

    def run_once(self, calendar, state, notifier):
        alerts.process(alerts.select_upcoming(calendar, NOW), NOW, notifier, state)

    def test_only_high_impact_in_horizon(self):
        cal = [make_event(20), make_event(20, impact="Low"),
               make_event(-10), make_event(60 * 24 * 30)]
        self.assertEqual(len(alerts.select_upcoming(cal, NOW)), 1)

    def test_min_impact_and_importance_order(self):
        cal = [make_event(20, impact="Low", title="L"),
               make_event(30, impact="High", title="H"),
               make_event(25, impact="Medium", title="M"),
               make_event(10, impact="Holiday", title="X")]
        old = cfg.ALERT_MIN_IMPACT
        try:
            cfg.ALERT_MIN_IMPACT = "low"
            titles = [e["title"] for _, e, _ in alerts.select_upcoming(cal, NOW)]
            self.assertEqual(titles, ["H", "M", "L"])  # importance first
            cfg.ALERT_MIN_IMPACT = "high"
            self.assertEqual(len(alerts.select_upcoming(cal, NOW)), 1)
            cfg.ALERT_MIN_IMPACT = "holiday"
            self.assertEqual(len(alerts.select_upcoming(cal, NOW)), 4)
        finally:
            cfg.ALERT_MIN_IMPACT = old

    def test_heading_matches_impact(self):
        n, st = FakeNotifier(), {}
        old = cfg.ALERT_MIN_IMPACT
        cfg.ALERT_MIN_IMPACT = "low"
        try:
            self.run_once([make_event(300, impact="Medium")], st, n)
        finally:
            cfg.ALERT_MIN_IMPACT = old
        self.assertIn("🟠 <b>MEDIUM-IMPACT EVENT DETECTED", n.sent[0])

    def test_no_duplicate_sends(self):
        n, st = FakeNotifier(), {}
        cal = [make_event(20)]
        self.run_once(cal, st, n)
        self.run_once(cal, st, n)
        self.assertEqual(len(n.sent), 2)  # detected + 30m, once each

    def test_one_hour_window(self):
        n, st = FakeNotifier(), {}
        self.run_once([make_event(50)], st, n)
        self.assertTrue(any("1-HOUR" in m for m in n.sent))
        self.assertFalse(any("30-MINUTE" in m for m in n.sent))

    def test_thirty_minute_window(self):
        n, st = FakeNotifier(), {}
        self.run_once([make_event(20)], st, n)
        self.assertTrue(any("30-MINUTE" in m for m in n.sent))

    def test_far_event_only_detected(self):
        n, st = FakeNotifier(), {}
        self.run_once([make_event(60 * 5)], st, n)
        self.assertEqual(len(n.sent), 1)
        self.assertIn("DETECTED", n.sent[0])

    def test_failed_send_is_retried_next_run(self):
        class Failing:
            def send(self, m):
                raise RuntimeError("boom")
        st = {}
        self.run_once([make_event(20)], st, Failing())
        n = FakeNotifier()
        self.run_once([make_event(20)], st, n)
        self.assertEqual(len(n.sent), 2)

    def test_message_escapes_html(self):
        ev = make_event(20, title="A <b> & B")
        msg = alerts.event_message(ev, parse_event_time(ev["date"]), "H")
        self.assertIn("A &lt;b&gt; &amp; B", msg)


class HelperTests(unittest.TestCase):
    def test_clean(self):
        for v in (None, "", " - ", "N/A", "null"):
            self.assertEqual(clean(v), "Not available")
        self.assertEqual(clean(" 3.2% "), "3.2%")

    def test_parse_event_time(self):
        self.assertIsNone(parse_event_time("garbage"))
        self.assertIsNone(parse_event_time(None))
        self.assertEqual(parse_event_time("2026-10-21T12:00:00Z"), NOW)

    def test_prune_state(self):
        st = {
            "USD|New|2026-10-21T15:00:00+00:00": {},
            "USD|Old|2026-10-01T15:00:00+00:00": {},
            "legacy-slug-key": {},
            "_telegram_update_offset": 5,
        }
        state_mod.prune_state(st, NOW)
        self.assertEqual(list(st), ["USD|New|2026-10-21T15:00:00+00:00"])


if __name__ == "__main__":
    unittest.main()
