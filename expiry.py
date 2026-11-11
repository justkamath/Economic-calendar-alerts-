"""Expiry dates for NSE and MCX instruments: what expires, which kind, and when.

NSE dates come from exchange rules (Tuesdays; a holiday moves it to the previous
trading day). MCX dates come from expiry_data.json (listed dates) plus simple
estimates for products with a steady pattern. Estimates are marked and every MCX
answer says to confirm on mcxindia.com. Nothing here needs the network.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone

import config as cfg

IST = timezone(timedelta(hours=5, minutes=30))
DATA_FILE = str(cfg.BASE_DIR / "expiry_data.json")

# key -> (emoji, button label, full name, exchange)
INSTRUMENTS = {
    "nifty": ("📈", "Nifty 50", "Nifty 50", "NSE"),
    "banknifty": ("🏦", "Bank Nifty", "Bank Nifty", "NSE"),
    "finnifty": ("💼", "Fin Nifty", "Fin Nifty", "NSE"),
    "midcap": ("🌱", "Midcap", "Midcap Nifty", "NSE"),
    "next50": ("🚀", "Next 50", "Nifty Next 50", "NSE"),
    "stockfo": ("🎟️", "Stock F&O", "Stock futures & options", "NSE"),
    "gold": ("🥇", "Gold", "Gold", "MCX"),
    "goldm": ("🥇", "Gold Mini", "Gold Mini", "MCX"),
    "silver": ("🥈", "Silver", "Silver", "MCX"),
    "silverm": ("🥈", "Silver Mini", "Silver Mini", "MCX"),
    "crude": ("🛢️", "Crude", "Crude oil", "MCX"),
    "crudem": ("🛢️", "Crude Mini", "Crude oil Mini", "MCX"),
    "natgas": ("🔥", "Nat Gas", "Natural gas", "MCX"),
}
ORDER = tuple(INSTRUMENTS)
MCX_NOTE = "Confirm MCX dates on mcxindia.com before you trade."
# words people type -> instrument (checked in this order)
ALIASES = (
    ("banknifty", r"bank ?nifty"), ("finnifty", r"fin ?nifty"), ("midcap", r"midcap"),
    ("next50", r"next ?50|nifty ?next"), ("stockfo", r"stocks?\b"),
    ("nifty", r"nifty"),
    ("goldm", r"gold ?mini|goldm\b"), ("silverm", r"silver ?(mini|micro)|silverm\b"),
    ("crudem", r"crude( ?oil)? ?mini|crudem\b"),
    ("gold", r"gold"), ("silver", r"silver"),
    ("natgas", r"natural ?gas|nat ?gas|natgas"), ("crude", r"crude"),
)
# MCX products with a steady pattern (estimates when nothing is listed)
MCX_DAY_OF_MONTH = {"gold": 5, "goldm": 5, "silver": 5, "crude": 19, "crudem": 19}
MCX_MONTHS = {"gold": {2, 4, 6, 8, 10, 12}, "silver": {3, 5, 7, 9, 12}}   # bi-monthly contracts
MCX_LAST_DAY = ("silverm",)

_cache = {}


def label(key):
    emoji, short, _, _ = INSTRUMENTS[key]
    return f"{emoji} {short}"


def find_instruments(text):
    """Instrument keys mentioned in a typed message, in picker order."""
    low = (text or "").lower()
    found = []
    for key, pattern in ALIASES:
        if re.search(pattern, low):
            found.append(key)
            low = re.sub(pattern, " ", low)    # so 'bank nifty' doesn't also match 'nifty'
    return [k for k in ORDER if k in found]


def load_data(path=None):
    path = path or DATA_FILE
    if path not in _cache:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        _cache[path] = data if isinstance(data, dict) else {}
    return _cache[path]


def _dates(items):
    out = set()
    for item in items or ():
        try:
            out.add(date.fromisoformat(item))
        except (TypeError, ValueError):
            pass
    return out


def previous_trading_day(day, holidays):
    while day.weekday() >= 5 or day in holidays:
        day -= timedelta(days=1)
    return day


def _month_start(day, step):
    month = day.month - 1 + step
    return date(day.year + month // 12, month % 12 + 1, 1)


def _last_weekday_of_month(first, weekday):
    nxt = _month_start(first, 1)
    day = nxt - timedelta(days=1)
    while day.weekday() != weekday:
        day -= timedelta(days=1)
    return day


def _estimated(day, data):
    return day.year not in set(data.get("holiday_years") or ())


def _nse(key, today, data):
    holidays = _dates(data.get("nse_holidays"))
    out, seen = [], set()

    def add(day, kind):
        day = previous_trading_day(day, holidays)
        if day >= today and (day, kind) not in seen:
            seen.add((day, kind))
            out.append({"date": day, "kind": kind, "est": _estimated(day, data)})

    for step in range(0, 4):      # monthly: last Tuesday of the month
        add(_last_weekday_of_month(_month_start(today, step), 1), "Monthly futures & options")
    monthly = {e["date"] for e in out}
    if key == "nifty":            # weekly options: every Tuesday
        tuesday = today + timedelta(days=(1 - today.weekday()) % 7)
        for week in range(0, 8):
            raw = tuesday + timedelta(days=7 * week)
            day = previous_trading_day(raw, holidays)
            if day >= today and day not in monthly:
                out.append({"date": day, "kind": "Weekly options", "est": _estimated(day, data)})
    elif key not in ("banknifty", "finnifty", "midcap", "next50", "stockfo"):
        return []
    return sorted(out, key=lambda e: e["date"])


def _mcx(key, today, data):
    """MCX commodities: listed dates first, then estimates from the usual pattern."""
    holidays = _dates(data.get("mcx_holidays"))
    out, taken = [], set()
    for row in data.get("mcx_expiries") or ():
        if row.get("i") != key:
            continue
        try:
            day = date.fromisoformat(row["d"])
        except (KeyError, TypeError, ValueError):
            continue
        if day >= today:
            out.append({"date": day, "kind": row.get("t", "Futures"),
                        "est": not row.get("listed", False)})
            taken.add((day.year, day.month))
    for step in range(0, 9):
        first = _month_start(today, step)
        if first.month not in MCX_MONTHS.get(key, range(1, 13)):
            continue
        if key in MCX_DAY_OF_MONTH:
            raw = date(first.year, first.month, MCX_DAY_OF_MONTH[key])
        elif key in MCX_LAST_DAY:
            raw = _month_start(first, 1) - timedelta(days=1)
        else:
            continue                      # no steady pattern: only listed dates
        day = previous_trading_day(raw, holidays)
        listed_near = any(e["kind"] == "Futures" and abs((e["date"] - day).days) <= 4 for e in out)
        if day >= today and not listed_near:
            out.append({"date": day, "kind": "Futures", "est": True})
    return sorted(out, key=lambda e: e["date"])


def upcoming(key, today, count=3, data=None):
    """The next `count` expiries for an instrument: [{date, kind, est}]."""
    data = load_data() if data is None else data
    rows = _nse(key, today, data) if INSTRUMENTS[key][3] == "NSE" else _mcx(key, today, data)
    return rows[:count]


# ---------- how it reads in Telegram ----------
NOTE = "~ is an estimate. Confirm with the exchange before you trade."


def _when(day, today):
    gap = (day - today).days
    if gap == 0:
        return "today"
    if gap == 1:
        return "tomorrow"
    return f"in {gap} days"


def _day_text(day):
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


CATEGORIES = (("Weekly", "🗓 Weekly"), ("Monthly", "🗓 Monthly"),
              ("Futures", "🗓 Futures"), ("Options", "🗓 Options"))


def category(kind):
    for name, _ in CATEGORIES:
        if kind.startswith(name):
            return name
    return "Futures"


def next_expiries(key, today, data=None):
    """{category: next row} for one instrument, e.g. Weekly, Monthly, Futures, Options."""
    out = {}
    for row in upcoming(key, today, 14, data):
        out.setdefault(category(row["kind"]), row)
    return out


def group_by_date(items, today=None):
    """[(date, est, [keys])] soonest first."""
    groups = {}
    for key, row in items:
        groups.setdefault((row["date"], row["est"]), []).append(key)
    return [(day, est, sorted(keys, key=ORDER.index))
            for (day, est), keys in sorted(groups.items(), key=lambda g: g[0])]


def _section(title, items, today):
    blocks = []
    for day, est, keys in group_by_date(items):
        names = ", ".join(label(k) for k in keys)
        blocks.append(f"<b>{'~' if est else ''}{_day_text(day)}</b> ({_when(day, today)})\n{names}")
    return f"<b>{title}</b>\n" + "\n\n".join(blocks)


def expiry_card(keys, today, count=None, data=None, title="📅 Next expiries"):
    """One section per kind of expiry, instruments sharing a date grouped, soonest first."""
    found = {name: [] for name, _ in CATEGORIES}
    missing = []
    for key in (k for k in ORDER if k in set(keys)):
        nxt = next_expiries(key, today, data)
        for name, row in nxt.items():
            found[name].append((key, row))
        if not nxt:
            missing.append(label(key))
    parts = [f"<b>{title}</b>"]
    for name, heading in CATEGORIES:
        if found[name]:
            parts.append(_section(heading, found[name], today))
    if missing:
        parts.append("<b>🕓 Not loaded yet</b>\n" + "\n".join(missing))
    if any(r["est"] for items in found.values() for _, r in items):
        parts.append(f"<i>{NOTE}</i>")
    return "\n\n".join(parts)


def today_ist(now):
    return now.astimezone(IST).date()
