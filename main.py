
import os
import json
import html
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
STATE_FILE = "sent_alerts.json"
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def fetch_calendar():
    request = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "EconomicCalendarAlerts/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not isinstance(data, list):
        raise RuntimeError("Unexpected Forex Factory JSON response.")

    return data


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Missing Telegram secrets.")

    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }).encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected message: {result}")

    print(
        "TELEGRAM_SENT message_id =",
        result.get("result", {}).get("message_id"),
    )


def parse_event_time(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def clean(value):
    if value is None:
        return "Not available"

    value = str(value).strip()
    if value.lower() in ("", "-", "—", "n/a", "na", "null", "none"):
        return "Not available"

    return value


def main():
    now = datetime.now(UTC)
    end = now + timedelta(days=30)

    print("Source: Forex Factory weekly JSON")
    print("Current UTC:", now.isoformat())
    print("Configured window: 30 days")
    print("Fetching calendar...")

    calendar = fetch_calendar()
    print("Events received:", len(calendar))

    matched = 0

    for event in calendar:
        if not isinstance(event, dict):
            continue

        impact = clean(event.get("impact")).lower()
        if impact != "high":
            continue

        event_time = parse_event_time(event.get("date"))
        if event_time is None:
            print("SKIP invalid timestamp:", event.get("title"))
            continue

        if not (now < event_time <= end):
            continue

        matched += 1

        name = clean(event.get("title"))
        country = clean(event.get("country"))
        time_ist = event_time.astimezone(IST).strftime(
            "%d-%m-%Y %I:%M %p"
        )

        details = (
            f"🌍 Country: {html.escape(country)}\n"
            f"📌 Event: <b>{html.escape(name)}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {html.escape(clean(event.get('previous')))}\n"
            f"🔮 Forecast: {html.escape(clean(event.get('forecast')))}\n"
            f"📈 Actual: {html.escape(clean(event.get('actual')))}\n"
            "Source: Forex Factory"
        )

        print("MATCH:", name, "| UTC:", event_time.isoformat())
        print("ACTION: sending diagnostic event alert")

        # Deliberately ignores saved alert state for this diagnostic run.
        send_telegram(
            "🧪 <b>DIAGNOSTIC EVENT ALERT</b>\n\n" + details
        )

    print("High-impact events matched:", matched)

    if matched == 0:
        print("No upcoming high-impact events were available in this feed.")

    print("Diagnostic run finished.")


if __name__ == "__main__":
    main()
