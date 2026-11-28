"""Typed questions about the calendar, like "high impact events next week" or
"all events 5 dec". Reading the question and building the answer happen here.

Asking never changes anyone's saved setup (impact level, currencies, expiries):
the answer is built from the question alone, using a person's own level and
currencies only as the default when the question does not say otherwise."""
import html
import re
from datetime import date, datetime, timedelta

import config as cfg
from sources.common import KNOWN_CURRENCIES
from summary import (FLAGS, ORDER as LEVEL_ORDER, RANK, TITLES, _chunk, _event_lines, _level,
                     _style)
from utils import parse_event_time

MAX_AHEAD_DAYS = 3650     # how far the "what do we hold" check looks: effectively everything stored
MAX_EVENTS = 300          # safety cap on one answer (several messages long); no limit on the dates
MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
WEEKDAYS = {d: i for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "ten": 10, "fourteen": 14}

_SUBJECT = re.compile(r"\b(events?|calendar|news|schedule|releases?|data)\b|what'?s on|whats on|"
                      r"what is on|what'?s happening|what is happening|what'?s coming")
_IMPACT = re.compile(r"\b(high|medium|low|holidays?)\b(?:\s+impact)?|\bimpact\b")
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|" \
         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),                              # 2026-12-05
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b(?:\s*,?\s*(\d{{4}}))?"),  # 5 dec
    re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s*,?\s*(\d{{4}}))?"),            # dec 5
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b"),                 # 5/12 (day first)
)
_WEEKDAY = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)(?:day|sday|nesday|rsday|urday|s|rs)?\b")
_RANGE_WORDS = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|this week|next week|this month|next month|"
    r"next\s+\w+\s+(?:days?|weeks?)|coming\s+\w+\s+days?|"
    r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?)\b")


def _month_number(name):
    return MONTHS[name[:3]]


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _find_dates(low, today):
    """Dates written in the text, in the order they appear: [(position, date)]."""
    found = []
    for index, pattern in enumerate(_DATE_PATTERNS):
        for m in pattern.finditer(low):
            g = m.groups()
            if index == 0:
                day = _safe_date(int(g[0]), int(g[1]), int(g[2]))
            elif index == 1:
                day = _resolve(int(g[0]), _month_number(g[1]), g[2], today)
            elif index == 2:
                day = _resolve(int(g[1]), _month_number(g[0]), g[2], today)
            else:
                day = _resolve(int(g[0]), int(g[1]), g[2], today)
            if day:
                found.append((m.start(), day, m.end()))
    found.sort()
    out, last_end = [], -1
    for start, day, end in found:       # drop overlaps (e.g. 2026-12-05 also looks like 12-05)
        if start >= last_end:
            out.append((start, day))
            last_end = end
    return out


def _resolve(day, month, year, today):
    if year:
        year = int(year)
        year += 2000 if year < 100 else 0
        return _safe_date(year, month, day)
    guess = _safe_date(today.year, month, day)
    if guess and (today - guess).days > 180:       # long gone this year: they mean next year
        guess = _safe_date(today.year + 1, month, day)
    return guess


def _count(word):
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word)


def _range(low, today):
    """(first day, last day) the question is about, or None if it names no time."""
    dates = _find_dates(low, today)
    if len(dates) >= 2:
        a, b = dates[0][1], dates[1][1]
        return (min(a, b), max(a, b))
    if dates:
        return (dates[0][1], dates[0][1])
    m = re.search(r"\b(?:next|coming)\s+(\w+)\s+(days?|weeks?)\b", low)
    if m and _count(m.group(1)):
        n = _count(m.group(1)) * (7 if m.group(2).startswith("week") else 1)
        return (today, today + timedelta(days=n - 1))
    if "next week" in low:
        monday = today + timedelta(days=7 - today.weekday())
        return (monday, monday + timedelta(days=6))
    if "this week" in low:
        return (today, today + timedelta(days=6 - today.weekday()))
    if "next month" in low:
        first = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        return (first, nxt - timedelta(days=1))
    if "this month" in low:
        nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        return (today, nxt - timedelta(days=1))
    if "tomorrow" in low:
        return (today + timedelta(days=1),) * 2
    if "yesterday" in low:
        return (today - timedelta(days=1),) * 2
    if re.search(r"\b(today|tonight)\b", low):
        return (today, today)
    m = _WEEKDAY.search(low)
    if m:
        ahead = (WEEKDAYS[m.group(1)] - today.weekday()) % 7
        return (today + timedelta(days=ahead),) * 2
    return None


def looks_like_query(text):
    """True for a typed question about calendar events (not a button or command)."""
    low = (text or "").lower()
    if not low or low.startswith("/") or "expir" in low:
        return False
    if _SUBJECT.search(low):
        return True
    return bool(re.search(r"\bimpact\b", low) and _range(low, date.today()))


def parse_query(text, today):
    """What the question asks: {start, end, levels, currencies, wide_currencies, span_cut}."""
    low = (text or "").lower()
    span = _range(low, today) or (today, today + timedelta(days=6))
    start, end = span
    levels = []
    for word in ("high", "medium", "low"):
        if re.search(rf"\b{word}\b", low):
            levels.append(word)
    if re.search(r"\bholidays?\b", low):
        levels.append("holiday")
    currencies = [c for c in re.findall(r"\b([a-z]{3})\b", low) if c.upper() in KNOWN_CURRENCIES]
    return {"start": start, "end": end, "levels": levels,
            "everything": bool(re.search(r"\b(all|every|everything)\b", low)) and not levels,
            "currencies": [c.upper() for c in dict.fromkeys(currencies)],
            "all_currencies": bool(re.search(r"\b(all|every) (the )?currenc", low))}


def _day_text(day):
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def _title(query, level_names, today):
    start, end = query["start"], query["end"]
    if start == end:
        when = {today: "today", today + timedelta(days=1): "tomorrow",
                today - timedelta(days=1): "yesterday"}.get(start, _day_text(start))
    else:
        when = f"{_day_text(start)} to {_day_text(end)}"
    return f"🔎 <b>{level_names}</b> · {when}"


def build_reply(query, calendar, now, own_level=None, own_cur=None, latest=None):
    """The answer as a list of Telegram messages. `calendar` is the event list for
    the asked days; `own_level`/`own_cur` are the person's saved setup, used only
    when the question does not name a level or currencies."""
    tz = cfg.DISPLAY_TZ
    today = now.astimezone(tz).date()
    if query["levels"]:
        wanted = set(query["levels"])
        names = " + ".join(TITLES[l].split()[0].capitalize() if l != "holiday" else "Holidays"
                           for l in LEVEL_ORDER if l in wanted)
        names += " impact" if not wanted == {"holiday"} else ""
    elif query["everything"]:
        wanted, names = None, "All events"
    else:
        floor = RANK.get(own_level, RANK["low"]) if own_level in cfg.IMPACT_RANK else RANK["low"]
        wanted = {l for l in cfg.IMPACT_RANK if RANK[l] >= floor and l != "holiday"}
        names = {"high": "High impact", "medium": "Medium + high", "low": "All events"}.get(
            own_level, "All events")
    if query["currencies"]:
        curs, cur_note = set(query["currencies"]), None
    elif query["all_currencies"] or not own_cur:
        curs, cur_note = None, None
    else:
        curs = set(own_cur)
        cur_note = "your currencies: " + " ".join(f"{FLAGS.get(c, '🌐')} {c}" for c in own_cur)
    rows = []
    for event in calendar:
        if not isinstance(event, dict):
            continue
        level = _level(event)
        if wanted is not None and level not in wanted:
            continue
        country = str(event.get("country") or "").upper()
        if curs is not None and country not in curs:
            continue
        when = parse_event_time(event.get("date"))
        if when is None:
            continue
        day = when.astimezone(tz).date()
        if not (query["start"] <= day <= query["end"]):
            continue
        rows.append((day, when, level, event))
    rows.sort(key=lambda r: (r[0], r[1], -RANK[r[2]]))
    head = [_title(query, names, today)]
    if cur_note:
        head.append(f"<i>{cur_note}. Say “all currencies” to see everything.</i>")
    if not rows:
        head.append("")
        if latest is not None and query["start"] > latest:
            head.append("📭 Nothing on the list for those days yet. It will be added soon.")
        else:
            head.append("😌 Nothing matches that.")
        head += _coverage_note(query, latest, today)
        return ["\n".join(head)]
    shown = rows[:MAX_EVENTS]
    lines, current = list(head) + [""], None
    for day, when, level, event in shown:
        if day != current:
            current = day
            lines += [f"📅 <b>{_day_text(day)}</b>"]
        lines += _event_lines(level, when, event)
    if len(rows) > len(shown):
        lines += [f"<i>+{len(rows) - len(shown)} more. Ask for a shorter stretch or a "
                  "higher impact to see them.</i>"]
    lines += _coverage_note(query, latest, today)
    return _chunk(lines)


def _coverage_note(query, latest, today):
    """Say so when the question reaches past what the calendar holds."""
    if latest is not None and query["end"] > latest:
        return ["", f"<i>📭 Events after {_day_text(latest)} aren't on the list yet. "
                    "They will be added soon.</i>"]
    return []
