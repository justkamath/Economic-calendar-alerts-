"""Run from the repo root:  python -m unittest discover -s tests -v"""
import os
import random
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import merge as mg  # noqa: E402
from models import Event  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
FF, TE, FMP = "forex_factory", "trading_economics", "fmp"


def ev(source, title, minutes=0, country="AUD", **kw):
    return Event(source=source, title=title, country=country,
                 time=T0 + timedelta(minutes=minutes), **kw)


class SimilarityTests(unittest.TestCase):
    def test_abbreviations_are_unified(self):
        self.assertEqual(mg.similarity("CPI m/m", "CPI MoM"), 1.0)
        self.assertEqual(mg.similarity("Non Farm Payrolls", "Nonfarm Payrolls"), 1.0)
        self.assertEqual(mg.similarity("GDP q/q", "Gross Domestic Product QoQ"), 1.0)

    def test_central_bank_names_are_recognised(self):
        self.assertEqual(mg.similarity("Federal Funds Rate", "Fed Interest Rate Decision"), 1.0)
        self.assertEqual(mg.similarity("Cash Rate", "RBA Interest Rate Decision"), 1.0)
        self.assertEqual(mg.similarity("Main Refinancing Rate", "ECB Interest Rate Decision"), 1.0)

    def test_different_events_score_low(self):
        self.assertLess(mg.similarity("Retail Sales y/y", "Unemployment Rate"), mg.THRESHOLD)
        self.assertEqual(mg.similarity("", "x"), 0.0)

    def test_period_and_qualifier_must_agree(self):
        self.assertFalse(mg._compatible("CPI m/m", "CPI y/y"))
        self.assertFalse(mg._compatible("German Prelim CPI m/m", "German CPI m/m"))
        self.assertFalse(mg._compatible("Final Manufacturing PMI", "Flash Manufacturing PMI"))
        self.assertTrue(mg._compatible("Prelim GDP q/q", "Preliminary GDP QoQ"))


class MergeTests(unittest.TestCase):
    def test_same_event_from_two_sources_becomes_one(self):
        out = mg.merge([ev(FF, "CPI m/m", forecast="0.7%"),
                        ev(TE, "CPI MoM", previous="0.4%")])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].source, out[0].title), (f"{FF}+{TE}", "CPI m/m"))

    def test_priority_wins_and_lower_sources_fill_gaps(self):
        out = mg.merge([
            ev(TE, "CPI MoM", impact="medium", forecast="0.9%", previous="0.4%", actual="0.8%"),
            ev(FF, "CPI m/m", impact="high", forecast="0.7%"),
        ])[0]
        self.assertEqual(out.impact, "high")          # FF outranks TE
        self.assertEqual(out.forecast, "0.7%")        # FF has it
        self.assertEqual(out.previous, "0.4%")        # only TE has it
        self.assertEqual(out.actual, "0.8%")          # only TE has it

    def test_custom_priority_changes_the_winner(self):
        out = mg.merge([ev(FF, "CPI m/m", forecast="0.7%"),
                        ev(TE, "CPI MoM", forecast="0.9%")], priority=(TE, FF))[0]
        self.assertEqual((out.title, out.forecast), ("CPI MoM", "0.9%"))

    def test_m_m_and_y_y_at_the_same_time_stay_separate(self):
        ff = [ev(FF, "CPI m/m"), ev(FF, "CPI y/y"), ev(FF, "Trimmed Mean CPI m/m")]
        te = [ev(TE, "CPI MoM", forecast="0.7%"), ev(TE, "CPI YoY", forecast="4.2%")]
        out = {e.title: e for e in mg.merge(ff + te)}
        self.assertEqual(len(out), 3)
        self.assertEqual(out["CPI m/m"].forecast, "0.7%")
        self.assertEqual(out["CPI y/y"].forecast, "4.2%")
        self.assertEqual(out["Trimmed Mean CPI m/m"].source, FF)   # nobody else has it

    def test_a_source_never_merges_with_itself(self):
        out = mg.merge([ev(FF, "Speaks"), ev(FF, "Speaks")])
        self.assertEqual(len(out), 2)

    def test_different_currency_or_far_apart_in_time_does_not_merge(self):
        self.assertEqual(len(mg.merge([ev(FF, "CPI m/m"), ev(TE, "CPI MoM", country="USD")])), 2)
        self.assertEqual(len(mg.merge([ev(FF, "CPI m/m"), ev(TE, "CPI MoM", minutes=60)])), 2)

    def test_small_time_difference_still_merges(self):
        self.assertEqual(len(mg.merge([ev(FF, "CPI m/m"), ev(TE, "CPI MoM", minutes=10)])), 1)

    def test_currency_case_is_ignored(self):
        self.assertEqual(len(mg.merge([ev(FF, "CPI m/m", country="aud"),
                                       ev(TE, "CPI MoM", country="AUD")])), 1)

    def test_oddly_named_lone_event_in_its_slot_still_matches(self):
        out = mg.merge([ev(FF, "Daylight Saving Time Shift", country="USD"),
                        ev(TE, "Clock Change", country="USD")])
        self.assertEqual(len(out), 1)

    def test_lone_event_does_not_steal_when_the_slot_is_crowded(self):
        ff = [ev(FF, "Retail Sales y/y"), ev(FF, "Unemployment Rate")]
        out = mg.merge(ff + [ev(TE, "Something Unrelated")])
        self.assertEqual(len(out), 3)

    def test_unmatched_events_are_kept(self):
        out = mg.merge([ev(FF, "A Event"), ev(TE, "Totally Different Thing", minutes=300)])
        self.assertEqual({e.source for e in out}, {FF, TE})

    def test_three_sources_merge_into_one(self):
        out = mg.merge([ev(FF, "CPI m/m"), ev(TE, "CPI MoM"), ev(FMP, "CPI (MoM)")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source, f"{FF}+{TE}+{FMP}")

    def test_unknown_sources_rank_last(self):
        out = mg.merge([ev("zzz", "CPI MoM", forecast="9%"), ev(FF, "CPI m/m", forecast="1%")])
        self.assertEqual(out[0].forecast, "1%")

    def test_result_does_not_depend_on_input_order(self):
        events = [ev(FF, "CPI m/m"), ev(FF, "CPI y/y"), ev(TE, "CPI MoM", forecast="0.7%"),
                  ev(TE, "CPI YoY", forecast="4.2%"), ev(FMP, "Cash Rate", minutes=30),
                  ev(TE, "RBA Interest Rate Decision", minutes=30, previous="4.60%")]
        expected = mg.merge(events)
        for seed in range(10):
            shuffled = events[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(mg.merge(shuffled), expected)

    def test_output_is_sorted_and_single_source_is_unchanged(self):
        events = [ev(FF, "B", minutes=60), ev(FF, "A", minutes=0)]
        out = mg.merge(events)
        self.assertEqual([e.title for e in out], ["A", "B"])
        self.assertEqual(out[0], events[1])

    def test_empty_input(self):
        self.assertEqual(mg.merge([]), [])

    def test_merged_events_feed_the_existing_alert_format(self):
        out = mg.merge([ev(FF, "CPI m/m", impact="high"), ev(TE, "CPI MoM", forecast="0.7%")])[0]
        d = out.to_calendar_dict()
        self.assertEqual((d["title"], d["impact"], d["forecast"]), ("CPI m/m", "High", "0.7%"))


if __name__ == "__main__":
    unittest.main()
