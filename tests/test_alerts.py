"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg  # noqa: E402
import alerts  # noqa: E402
import alert_message as am  # noqa: E402
from alert_message import event_message, heading  # noqa: E402

WHEN = datetime(2026, 11, 6, 13, 30, tzinfo=cfg.UTC)  # 7:00 PM IST, Fri 6 Nov
NOW = WHEN - timedelta(hours=2, minutes=15)


def ev(**kw):
    base = {"title": "Non Farm Payrolls", "country": "USD", "impact": "High",
            "date": WHEN.isoformat(), "previous": "29K", "forecast": "", "actual": ""}
    base.update(kw)
    return base


def msg_for(event=None, now=NOW, head="H"):
    return event_message(event or ev(), WHEN, head, now=now)


def table_of(message):
    return message.split("<pre>")[1].split("</pre>")[0]


class AlertMessageTests(unittest.TestCase):
    def setUp(self):
        self._old = cfg.DISPLAY_TZ, cfg.DISPLAY_TZ_LABEL, cfg.SOURCE_NAME
        cfg.DISPLAY_TZ, cfg.DISPLAY_TZ_LABEL = ZoneInfo("Asia/Kolkata"), "IST"
        cfg.SOURCE_NAME = "Forex Factory"

    def tearDown(self):
        cfg.DISPLAY_TZ, cfg.DISPLAY_TZ_LABEL, cfg.SOURCE_NAME = self._old

    def test_headings_keep_level_colour_and_name(self):
        self.assertEqual(heading("detected", "high"),
                         "🔴 <b>HIGH IMPACT</b>\n🔔 <i>New on your calendar</i>")
        self.assertEqual(heading("1h", "medium"),
                         "🟠 <b>MEDIUM IMPACT</b>\n⏰ <i>Starts in about 1 hour</i>")
        self.assertEqual(heading("30m", "low"),
                         "🟡 <b>LOW IMPACT</b>\n⏳ <i>Starts in about 30 minutes</i>")
        self.assertIn("BANK HOLIDAY", heading("detected", "holiday"))

    def test_exact_card_layout(self):
        e = ev(title="German Prelim CPI m/m", country="EUR", impact="Medium",
               previous="0.6%", forecast="0.1%")
        self.assertEqual(msg_for(e, head=heading("detected", "medium")), (
            "🟠 <b>MEDIUM IMPACT</b>\n"
            "🔔 <i>New on your calendar</i>\n\n"
            "<b>German Prelim CPI m/m</b>\n"
            "🇪🇺 EUR (Euro)\n\n"
            "🕒 <b>7:00 PM IST</b>\n"
            "📅 Today, Fri 06 Nov\n"
            "⏳ In 2 hr 15 min\n\n"
            "<pre>Prior     0.6%\n"
            "Forecast  0.1%  ▼ down\n"
            "Actual    not out yet</pre>\n"
            "<i>🌿 Just a heads-up, nothing to do right now.</i>\n"
            "<i>Source: Forex Factory</i>"))

    def test_numbers_start_in_the_same_column(self):
        for e in (ev(), ev(previous="1.2%", forecast="1.5%", actual="1.6%")):
            for line in table_of(msg_for(e)).split("\n"):
                self.assertEqual(line[8:10], "  ", line)
                self.assertNotEqual(line[10], " ", line)

    def test_no_box_drawing_or_dot_separators(self):
        m = msg_for(ev(), head=heading("detected", "high"))
        for ch in "│╭╰├─·":
            self.assertNotIn(ch, m)

    def test_no_figures_gets_a_friendly_line_instead_of_empty_rows(self):
        m = msg_for(ev(previous="", impact="Holiday", country="JPY"))
        self.assertIn("No figures published for this one.", m)
        self.assertNotIn("<pre>", m)
        self.assertNotIn("n/a", m)

    def test_currency_name_shown_for_known_currencies_only(self):
        self.assertIn("🇺🇸 USD (US Dollar)", msg_for())
        self.assertIn("🌐 XYZ\n", msg_for(ev(country="XYZ")))

    def test_actual_compared_with_forecast(self):
        self.assertIn("0.3%  ▲ above",
                      table_of(msg_for(ev(previous="0.6%", forecast="0.1%", actual="0.3%"))))
        self.assertIn("▼ below", msg_for(ev(forecast="0.5%", actual="0.3%")))
        self.assertIn("= on forecast", msg_for(ev(forecast="0.3%", actual="0.3%")))

    def test_trend_arrows(self):
        self.assertIn("▲ up", msg_for(ev(previous="1%", forecast="2%")))
        self.assertIn("▼ down", msg_for(ev(previous="2%", forecast="1%")))
        self.assertIn("= flat", msg_for(ev(previous="2%", forecast="2%")))
        self.assertNotIn("▲", msg_for(ev(previous="word", forecast="other")))

    def test_number_parsing(self):
        self.assertEqual(am._number("0.6%"), 0.6)
        self.assertEqual(am._number("29.0K"), 29000.0)
        self.assertEqual(am._number("-1.2B"), -1.2e9)
        self.assertEqual(am._number("1,250"), 1250.0)
        self.assertIsNone(am._number("Hawkish"))

    def test_day_labels_and_countdown(self):
        self.assertIn("Today, Fri 06 Nov", msg_for(now=WHEN - timedelta(hours=1)))
        self.assertIn("Tomorrow, Fri 06 Nov", msg_for(now=WHEN - timedelta(hours=26)))
        self.assertIn("📅 Fri 06 Nov", msg_for(now=WHEN - timedelta(days=4)))
        self.assertIn("⏳ In 45 min", msg_for(now=WHEN - timedelta(minutes=45)))
        self.assertIn("⏳ In 1 hour\n", msg_for(now=WHEN - timedelta(hours=1)))
        self.assertIn("⏳ In 2 hours\n", msg_for(now=WHEN - timedelta(hours=2)))
        self.assertIn("⏳ In 1 hr 15 min", msg_for(now=WHEN - timedelta(hours=1, minutes=15)))
        self.assertIn("⏳ In 3 days 4 hrs", msg_for(now=WHEN - timedelta(days=3, hours=4)))
        self.assertIn("⏳ In 1 day 0 hrs", msg_for(now=WHEN - timedelta(days=1)))
        self.assertIn("⏳ Starting now", msg_for(now=WHEN + timedelta(minutes=2)))

    def test_missing_numbers_show_na_and_keep_rows(self):
        t = table_of(msg_for(ev(previous="", forecast="2%")))
        self.assertIn("Prior     n/a", t)
        self.assertIn("Forecast  2%", t)
        self.assertIn("Actual    not out yet", t)
        self.assertNotIn("Not available", t)

    def test_unknown_country_gets_globe(self):
        self.assertIn("🌐 XYZ", msg_for(ev(country="XYZ")))

    def test_escapes_html(self):
        m = msg_for(ev(title="A <b> & B", previous="<1"))
        self.assertIn("A &lt;b&gt; &amp; B", m)
        self.assertIn("&lt;1", table_of(m))

    def test_works_without_passing_now(self):
        self.assertIn("⏳ ", event_message(ev(), WHEN, "H"))

    def test_alerts_module_uses_the_new_layout(self):
        self.assertIs(alerts.event_message, event_message)


if __name__ == "__main__":
    unittest.main()
