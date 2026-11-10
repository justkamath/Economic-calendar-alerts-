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
    "nsegold": ("🪙", "NSE Gold", "NSE Gold (1 kg)", "NSE"),
    "nsegoldm": ("💰", "NSE Gold Mini", "NSE Gold Mini", "NSE"),
    "nsegold10": ("🏅", "NSE Gold 10g", "NSE Gold 10 grams", "NSE"),
    "nseguinea": ("👑", "NSE Guinea", "NSE Gold Guinea", "NSE"),
    "gold": ("🥇", "Gold", "Gold", "MCX"),
    "silver": ("🥈", "Silver", "Silver", "MCX"),
    "crude": ("🛢️", "Crude", "Crude oil", "MCX"),
    "natgas": ("🔥", "Nat Gas", "Natural gas", "MCX"),
    "copper": ("🟠", "Copper", "Copper", "MCX"),
    "zinc": ("🧱", "Zinc", "Zinc", "MCX"),
    "aluminium": ("🥫", "Aluminium", "Aluminium", "MCX"),
    "lead": ("🔋", "Lead", "Lead", "MCX"),
}
ORDER = tuple(INSTRUMENTS)
MCX_NOTE = "Confirm MCX dates on mcxindia.com before you trade."
NSE_COMMODITY = ("nsegold", "nsegoldm", "nsegold10", "nseguinea")
NSE_NOTE = "Confirm NSE commodity dates on nseindia.com before you trade."
# words people type -> instrument (checked in this order)
ALIASES = (
    ("nsegoldm", r"nse gold ?mini|goldm\b"), ("nsegold10", r"gold ?10"), ("nseguinea", r"guinea"),
    ("nsegold", r"nse gold"),
    ("banknifty", r"bank ?nifty"), ("finnifty", r"fin ?nifty"), ("midcap", r"midcap"),
    ("next50", r"next ?50|nifty ?next"), ("stockfo", r"stocks?\b"),
    ("nifty", r"nifty"), ("gold", r"gold"), ("silver", r"silver"),
    ("natgas", r"natural ?gas|nat ?gas|natgas"), ("crude", r"crude"),
    ("copper", r"copper"), ("zinc", r"zinc"), ("aluminium", r"alumini?um"), ("lead", r"\blead\b"),
)
# MCX products with a steady monthly pattern (estimates when nothing is listed)
MCX_DAY_OF_MONTH = {"gold": 5, "silver": 5, "crude": 19}
MCX_LAST_DAY = ("copper", "zinc", "aluminium", "lead", "nsegold10", "nseguinea")
MCX_DAY_OF_MONTH["nsegold"] = 5

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


def _third_last_business_day(first, holidays):
    day = _month_start(first, 1) - timedelta(days=1)
    count = 0
    while True:
        if day.weekday() < 5 and day not in holidays:
            count += 1
            if count == 3:
                return day
        day -= timedelta(days=1)


def _mcx(key, today, data):
    """MCX products, and NSE commodity products (which use the NSE holiday list)."""
    holidays = _dates(data.get("nse_holidays" if key in NSE_COMMODITY else "mcx_holidays"))
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
    for step in range(0, 4):
        first = _month_start(today, step)
        if key in MCX_DAY_OF_MONTH:
            raw = date(first.year, first.month, MCX_DAY_OF_MONTH[key])
        elif key in MCX_LAST_DAY:
            raw = _month_start(first, 1) - timedelta(days=1)
        elif key == "nsegoldm":
            raw = _third_last_business_day(first, holidays)
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
    equity = INSTRUMENTS[key][3] == "NSE" and key not in NSE_COMMODITY
    rows = _nse(key, today, data) if equity else _mcx(key, today, data)
    return rows[:count]


# ---------- how it reads in Telegram ----------
def _when(day, today):
    gap = (day - today).days
    if gap == 0:
        return "today"
    if gap == 1:
        return "tomorrow"
    return f"in {gap} days"


def _day_text(day):
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def instrument_lines(key, today, count=3, data=None):
    emoji, _, name, exchange = INSTRUMENTS[key]
    rows = upcoming(key, today, count, data)
    lines = [f"<b>{emoji} {name}</b> <i>({exchange})</i>"]
    if not rows:
        lines.append("No date loaded yet")
    for row in rows:
        mark = "~" if row["est"] else ""
        lines.append(f"{row['kind']}: <b>{mark}{_day_text(row['date'])}</b> ({_when(row['date'], today)})")
    return "\n".join(lines)


def expiry_card(keys, today, count=3, data=None, title="📅 Expiry dates"):
    keys = [k for k in ORDER if k in set(keys)]
    parts = [f"<b>{title}</b>"]
    parts += [instrument_lines(k, today, count, data) for k in keys]
    notes = []
    if any(INSTRUMENTS[k][3] == "MCX" for k in keys):
        notes.append(MCX_NOTE)
    if any(k in NSE_COMMODITY for k in keys):
        notes.append(NSE_NOTE)
    shown = [r for k in keys for r in upcoming(k, today, count, data)]
    if any(r["est"] for r in shown):
        notes.append("~ means an estimate from the usual pattern.")
    if notes:
        parts.append("<i>" + " ".join(notes) + "</i>")
    return "\n\n".join(parts)


def today_ist(now):
    return now.astimezone(IST).date()
