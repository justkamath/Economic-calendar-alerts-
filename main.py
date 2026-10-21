
import os
import json
import html
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

STATE_FILE = "sent_alerts.json"
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
BASE_URL = "https://www.forexfactory.com/calendar"


class CalendarParser(HTMLParser):
    """Extract calendar rows from Forex Factory HTML."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None
        self.cell = None
        self.in_table = False
        self.table_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()

        if tag == "table" and "calendar__table" in classes:
            self.in_table = True
            self.table_depth = 1
            return

        if not self.in_table:
            return

        if tag == "table":
            self.table_depth += 1

        if tag == "tr" and (
            "calendar__row" in classes or "calendar_row" in classes
        ):
            self.current = {}
            self.cell = None

        if tag == "td" and self.current is not None:
            for field in (
                "date", "time", "currency", "impact",
                "event", "actual", "forecast", "previous"
            ):
                if f"calendar__{field}" in classes:
                    self.cell = field
                    self.current.setdefault(field, "")
                    break

        if self.cell == "impact" and tag == "span":
            self.current["_impact_title"] = attrs.get("title", "")

    def handle_data(self, data):
        if self.current is not None and self.cell:
            self.current[self.cell] = (
                self.current.get(self.cell, "") + data
            )

    def handle_endtag(self, tag):
        if not self.in_table:
            return

        if tag == "td":
            self.cell = None

        if tag == "tr" and self.current is not None:
            self.rows.append(self.current)
            self.current = None
            self.cell = None

        if tag == "table":
            self.table_depth -= 1
            if self.table_depth <= 0:
                self.in_table = False


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def fetch_page(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", errors="replace")

    if "calendar__table" not in page:
        raise RuntimeError(
            "Forex Factory calendar table was not found. "
            "The site may have blocked the request or changed its page."
        )

    parser = CalendarParser()
    parser.feed(page)

    if not parser.rows:
        raise RuntimeError(
            "No calendar rows were parsed. Refusing to treat "
            "the calendar check as successful."
        )

    return parser.rows


def clean(value):
    value = " ".join(str(value or "").split())
    if not value or value.lower() in {
        "-", "—", "n/a", "na", "none", "null"
    }:
        return "Not available"
    return value


def parse_calendar_datetime(date_text, time_text, year):
    date_text = clean(date_text)
    time_text = clean(time_text)

    if date_text == "Not available":
        return None

    # Forex Factory calendar dates commonly look like "Mon Oct 19".
    for fmt in ("%a %b %d %Y", "%b %d %Y"):
        try:
            day = datetime.strptime(
                f"{date_text} {year}", fmt
            ).date()
            break
        except ValueError:
            day = None

    if day is None:
        return None

    if time_text.lower() in ("all day", "day", "not available"):
        time_text = "12:00am"

    try:
        clock = datetime.strptime(
            time_text.lower().replace(" ", ""), "%I:%M%p"
        ).time()
    except ValueError:
        return None

    # Calendar display time is not guaranteed to be UTC.
    # This assumes the page's displayed timezone is UTC.
    return datetime.combine(day, clock, tzinfo=UTC)


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }).encode()

    request = urllib.request.Request(url, data=body)

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram send failed: {result}")


def main():
    now = datetime.now(UTC)
    end = now + timedelta(days=30)
    state = load_state()

    # Fetch five consecutive calendar weeks.
    week_start = now.date() - timedelta(days=now.weekday())
    weeks = []
    cursor = week_start

    while cursor <= end.date():
        weeks.append(cursor)
        cursor += timedelta(days=7)

    events = {}
    for week in weeks:
        week_label = week.strftime("%b%d.%Y").lower()
        url = f"{BASE_URL}?week={week_label}"
        print("Fetching Forex Factory:", url)

        rows = fetch_page(url)

        current_date = ""
        for row in rows:
            if clean(row.get("date")) != "Not available":
                current_date = clean(row.get("date"))

            impact = (
                row.get("_impact_title", "")
                + " "
                + row.get("impact", "")
            ).lower()

            if "high" not in impact and "red" not in impact:
                continue

            name = clean(row.get("event"))
            currency = clean(row.get("currency"))
            if name == "Not available":
                continue

            event_dt = parse_calendar_datetime(
                current_date, row.get("time"), week.year
            )
            if event_dt is None:
                print("Skipping event with unrecognized date/time:", name)
                continue

            if not (now < event_dt <= end):
                continue

            key = f"{currency}|{name}|{event_dt.isoformat()}"
            events[key] = {
                "name": name,
                "currency": currency,
                "time": event_dt,
                "previous": clean(row.get("previous")),
                "forecast": clean(row.get("forecast")),
                "actual": clean(row.get("actual")),
            }

        time.sleep(1)

    if not events:
        raise RuntimeError(
            "No upcoming high-impact events were parsed. "
            "Check the Forex Factory page format and timezone."
        )

    print("Upcoming high-impact events:", len(events))

    for key, event in events.items():
        status = state.setdefault(key, {
            "detected": False,
            "1h": False,
            "30m": False,
        })

        minutes_left = (
            event["time"] - now
        ).total_seconds() / 60

        time_ist = event["time"].astimezone(IST).strftime(
            "%d-%m-%Y %I:%M %p"
        )

        details = (
            f"🌍 Currency: {html.escape(event['currency'])}\n"
            f"📌 Event: <b>{html.escape(event['name'])}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {html.escape(event['previous'])}\n"
            f"🔮 Forecast: {html.escape(event['forecast'])}\n"
            f"📈 Actual: {html.escape(event['actual'])}"
        )

        source = (
            '\nSource: <a href="https://www.forexfactory.com/calendar">'
            'Forex Factory</a>'
        )

        if not status["detected"]:
            send_telegram(
                "🔴 <b>HIGH-IMPACT EVENT DETECTED</b>\n\n"
                + details + source
            )
            status["detected"] = True
            save_state(state)

        if 55 <= minutes_left <= 65 and not status["1h"]:
            send_telegram(
                "⏰ <b>1-HOUR REMINDER</b>\n\n"
                + details + source
            )
            status["1h"] = True
            save_state(state)

        if 25 <= minutes_left <= 35 and not status["30m"]:
            send_telegram(
                "⏰ <b>30-MINUTE REMINDER</b>\n\n"
                + details + source
            )
            status["30m"] = True
            save_state(state)

    print("Calendar check completed.")


if __name__ == "__main__":
    main()
