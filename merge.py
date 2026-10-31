"""Combine the same event reported by several sources into one.

Rules (kept simple on purpose, so they are easy to trust and to tune):
  * Only events of the same currency, within TIME_TOLERANCE of each other, can match.
  * A source never matches itself: two events from one source are always two events.
  * Titles must look alike (see `similarity`), and agree on period (m/m, y/y, q/q)
    and qualifier (prelim, flash, final, ...), so "CPI m/m" never merges with "CPI y/y".
  * If titles are written very differently but a source has exactly one event in a
    slot (same currency, same minute) that nobody else claimed, they still match.
  * For each field, the highest-priority source that has a value wins; lower
    sources only fill gaps. Events nobody else reports stay as they are.

If two names for one event are not recognised as the same, add them to ALIASES."""
import re
from datetime import timedelta
from difflib import SequenceMatcher

from models import Event

DEFAULT_PRIORITY = ("forex_factory", "trading_economics", "fmp", "finnhub")
TIME_TOLERANCE = timedelta(minutes=15)
THRESHOLD = 0.6            # how alike two titles must be (0 to 1)

# phrase (as it looks after cleaning) -> the one form we compare on
ALIASES = (
    ("non farm", "nonfarm"),
    ("preliminary", "prelim"),
    ("gross domestic product", "gdp"),
    ("consumer price index", "cpi"),
    ("producer price index", "ppi"),
    ("fed interest rate decision", "federal funds rate"),
    ("fomc interest rate decision", "federal funds rate"),
    ("ecb interest rate decision", "main refinancing rate"),
    ("boe interest rate decision", "official bank rate"),
    ("boj interest rate decision", "boj policy rate"),
    ("rba interest rate decision", "cash rate"),
    ("rbnz interest rate decision", "official cash rate"),
    ("boc interest rate decision", "overnight rate"),
    ("snb interest rate decision", "snb policy rate"),
)
_STOPWORDS = {"the", "of", "and", "s"}
_PERIODS = {"mom", "yoy", "qoq", "wow"}
_QUALIFIERS = {"prelim", "flash", "final", "advance", "revised", "second", "third"}


def normalise(title):
    """Lower-case words of a title with abbreviations and aliases unified."""
    text = str(title).lower()
    for short, word in (("m/m", " mom "), ("y/y", " yoy "), ("q/q", " qoq "),
                        ("w/w", " wow "), ("&", " and ")):
        text = text.replace(short, word)
    text = " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
    for phrase, canonical in ALIASES:
        text = re.sub(rf"\b{phrase}\b", canonical, text)
    return [w for w in text.split() if w not in _STOPWORDS]


def similarity(a, b):
    """0 (nothing alike) to 1 (same) between two titles."""
    ta, tb = normalise(a), normalise(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(set(ta) & set(tb)) / len(set(ta) | set(tb))
    return max(jaccard, SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio())


def _compatible(a, b):
    """Same period and same qualifier (or none on both sides)."""
    ta, tb = set(normalise(a)), set(normalise(b))
    return (ta & _PERIODS) == (tb & _PERIODS) and (ta & _QUALIFIERS) == (tb & _QUALIFIERS)


def _same_slot(a, b, tolerance):
    return a.country.lower() == b.country.lower() and abs(a.time - b.time) <= tolerance


def _score(event, members, tolerance):
    """Best match quality between an event and a group's members, or -1."""
    best = -1.0
    for m in members:
        if not _same_slot(event, m, tolerance) or not _compatible(event.title, m.title):
            continue
        minutes = abs(event.time - m.time).total_seconds() / 60
        best = max(best, similarity(event.title, m.title) - minutes * 0.001)
    return best


def _rank(source, priority):
    return (priority.index(source) if source in priority else len(priority), source)


def _combine(members):
    """One Event from a group; members are already in priority order."""
    def first(field):
        return next((getattr(m, field) for m in members if getattr(m, field)), None)

    best = members[0]
    return Event(source="+".join(m.source for m in members), title=best.title,
                 country=best.country, time=best.time, impact=first("impact") or "",
                 previous=first("previous"), forecast=first("forecast"),
                 actual=first("actual"))


def merge(events, priority=DEFAULT_PRIORITY, tolerance=TIME_TOLERANCE,
          threshold=THRESHOLD):
    """Merge events from any number of sources. Returns Events sorted by time."""
    by_source = {}
    for e in events:
        by_source.setdefault(e.source, []).append(e)

    groups = []   # each group is a list of Events, highest-priority source first
    for source in sorted(by_source, key=lambda s: _rank(s, priority)):
        items = sorted(by_source[source], key=lambda e: (e.time, e.title))
        pairs = sorted(
            (-s, i, j)
            for i, e in enumerate(items) for j, g in enumerate(groups)
            if (s := _score(e, g, tolerance)) >= threshold)
        taken_items, taken_groups = set(), set()
        for _, i, j in pairs:                       # best matches first, one-to-one
            if i not in taken_items and j not in taken_groups:
                groups[j].append(items[i])
                taken_items.add(i)
                taken_groups.add(j)

        # Different wording, but alone in its slot on both sides: still the same event.
        loose_items = [i for i in range(len(items)) if i not in taken_items]
        loose_groups = [j for j in range(len(groups)) if j not in taken_groups]
        for i in loose_items:
            same = [j for j in loose_groups
                    if _same_slot(items[i], groups[j][0], timedelta(0))]
            rivals = [k for k in loose_items
                      if _same_slot(items[k], items[i], timedelta(0))]
            if (len(same) == 1 and len(rivals) == 1
                    and _compatible(items[i].title, groups[same[0]][0].title)):
                groups[same[0]].append(items[i])
                taken_items.add(i)
                loose_groups.remove(same[0])

        groups += [[items[i]] for i in range(len(items)) if i not in taken_items]

    merged = [_combine(g) for g in groups]
    return sorted(merged, key=lambda e: (e.time, e.title, e.source))
