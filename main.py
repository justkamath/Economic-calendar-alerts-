
import os
import json
import html
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

BASE_URL = "https://www.forexfactory.com/calendar"
STATE_FILE = "sent_alerts.json"
IST = ZoneInfo("Asia/Kolkata")


class CalendarParser(HTMLParser):
    """Parse event rows from the Forex Factory calendar table."""

    FIELDS = (
        "date", "time", "currency", "impact",
        "event", "actual", "forecast", "previous"
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.current = None
        self.cell = None
        self.table_found = False
        self.table_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()

        if tag == "table" and "calendar__table" in classes:
            self.table_found = True
            self.table_depth = 1
            return

        if not self.table_found:
            return

        if tag == "table":
            self.table_depth += 1

        # Forex Factory event rows use both classes.
        if tag == "tr" and {
            "calendar__row", "calendar_row"
        }.issubset(set(classes)):
            self.current = {"_impact_classes": ""}
            self.cell = None

        if self.current is None:
            return

        if tag == "td":
            self.cell = None
            for field in self.FIELDS:
                if (
                    f"calendar__{field}" in classes
                    and "calendar__cell" in classes
                ):
                    self.cell = field
                    self.current.setdefault(field, "")
                    break

            if self.cell == "impact":
                self.current["_impact_classes"] += " " + " ".join(classes)

        # High impact is usually indicated by a red impact icon/class.
        if tag in ("span", "i", "img"):
            details = " ".join((
                attrs.get("class", ""),
                attrs.get("title", ""),
                attrs.get("alt", ""),
            ))
            self.current["_impact_classes"] += " " + details

    def handle_data(self, data):
        if self.current is not None and self.cell:
            self.current[self.cell] = (
                self.current.get(self.cell, "") + data
            )

    def handle_endtag(self, tag):
        if not self.table_found:
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
                self.table_found = False


def clean(value):
    value = " ".join(str(value or "").split())
    if not value or value.lower() in {
        "-", "—", "n/a", "na", "none", "null"
    }:
        return "Not available"
    return value


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


def fetch_week(week_date):
    week_label = week_date.strftime("%b%d.%Y").lower()
    url = f"{BASE_URL}?week={week_label}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            # Ask Forex Factory to display calendar times in India time.
            "Cookie": "timezone=Asia/Kolkata",
        },
    )

    print("Fetching:", url)

    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode("utf-8", errors="replace")

    parser = CalendarParser()
    parser.feed(page)

    if not parser.rows:
        raise RuntimeError(
            "No Forex Factory calendar rows were found. "
            "The request may be blocked or the page format may have changed."
        )

    return parser.rows


def parse_event_datetime(date_text, time_text, week_date):
    date_text = clean(date_text)
    time_text = clean(time_text)

    if date_text == "Not available":
        return None

    # Calendar dates commonly appear as "Mon Oct 19".
    parsed_date = None
    for year in (week_date.year, week_date.year - 1, week_date.year + 1):
        for fmt in ("%a %b %d %Y", "%b %d %Y"):
            try:
                parsed_date = datetime.strptime(
                    f"{date_text} {year}", fmt
                ).date()
                break
            except ValueError:
                pass
        if parsed_date is not None:
            break

    if parsed_date is None:
        return None

    if time_text.lower() in {
        "not available", "all day", "tentative", "day"
    }:
        # Do not invent a precise release time for untimed events.
        return None

    try:
        clock = datetime.strptime(
            time_text.replace(" ", "").lower(), "%I:%M%p"
        ).time()
    except ValueError:
        return None

    # This relies on the timezone cookie being honored by the site.
    return datetime.combine(parsed_date, clock, tzinfo=IST)


def is_high_impact(row):
    impact = row.get("_impact_classes", "").lower()
    return any(marker in impact for marker in (
        "ff-impact-red",
        "impact--high",
        "impact-red",
        "high impact",
    ))


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Telegram bot secrets are missing.")

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
        raise RuntimeError(f"Telegram API error: {result}")


def main():
    now = datetime.now(IST)
    end = now + timedelta(days=30)
    state = load_state()
    events = {}

    # Fetch the calendar for every week touched by the 30-day range.
    week_date = now.date() - timedelta(days=now.weekday())
    final_date = end.date()

    while week_date <= final_date:
        rows = fetch_week(week_date)
        current_date = ""

        for row in rows:
            date_value = clean(row.get("date"))
            if date_value != "Not available":
                current_date = date_value

            if not is_high_impact(row):
                continue

            name = clean(row.get("event"))
            currency = clean(row.get("currency"))
            if name == "Not available":
                continue

            event_time = parse_event_datetime(
                current_date, row.get("time"), week_date
            )
            if event_time is None:
                print("Skipping event with unknown date/time:", name)
                continue

            if not (now < event_time <= end):
                continue

            key = f"{currency}|{name}|{event_time.isoformat()}"

            events[key] = {
                "name": name,
                "currency": currency,
                "time": event_time,
                "previous": clean(row.get("previous")),
                "forecast": clean(row.get("forecast")),
                "actual": clean(row.get("actual")),
            }

        week_date += timedelta(days=7)

    if not events:
        raise RuntimeError(
            "No upcoming high-impact events were parsed. "
            "Do not rely on this run until the parser and timezone "
            "have been checked against the live calendar."
        )

    print("Upcoming high-impact events:", len(events))

    for key, event in sorted(
        events.items(), key=lambda item: item[1]["time"]
    ):
        status = state.setdefault(key, {
            "detected": False,
            "1h": False,
            "30m": False,
        })

        minutes_left = (
            event["time"] - now
        ).total_seconds() / 60

        time_ist = event["time"].strftime("%d-%m-%Y %I:%M %p")

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

    print("Calendar check completed successfully.")


if __name__ == "__main__":
    main()
