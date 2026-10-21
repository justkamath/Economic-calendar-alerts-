
import json
import os
import html
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
STATE_FILE = "sent_alerts.json"
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


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


def clean(value):
    if value is None:
        return "Not available"
    value = str(value).strip()
    if value.lower() in ("", "-", "—", "n/a", "null", "none"):
        return "Not available"
    return value


def parse_event_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Forex Factory's published feed uses UTC timestamps.
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def fetch_calendar():
    request = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "EconomicCalendarAlerts/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not isinstance(data, list):
        raise RuntimeError("Forex Factory returned an unexpected JSON format.")

    return data


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")

    import urllib.parse

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
        raise RuntimeError(f"Telegram API error: {result}")


def main():
    now = datetime.now(UTC)
    end = now + timedelta(days=30)
    state = load_state()

    print("Source: Forex Factory weekly JSON")
    print("Current UTC time:", now.isoformat())
    print("Configured window: next 30 days")

    calendar = fetch_calendar()
    print("Events received:", len(calendar))

    if not calendar:
        raise RuntimeError("Forex Factory returned an empty calendar.")

    upcoming = []
    for event in calendar:
        if not isinstance(event, dict):
            continue

        if clean(event.get("impact")).lower() != "high":
            continue

        event_time = parse_event_time(event.get("date"))
        if event_time is None or not (now < event_time <= end):
            continue

        name = clean(event.get("title"))
        country = clean(event.get("country"))
        key = f"{country}|{name}|{event_time.isoformat()}"

        upcoming.append((key, event, event_time))

    
    print("Upcoming high-impact events in available feed:", len(upcoming))

    for key, event, event_time in upcoming:
        print(
            "MATCH:",
            event.get("title"),
            "| impact:", event.get("impact"),
            "| UTC:", event_time.isoformat(),
            "| IST:", event_time.astimezone(IST).isoformat(),
            "| saved:", key in state,
        )

    for key, event, event_time in sorted(upcoming, key=lambda item: item[2]):
        status = state.setdefault(key, {
            "detected": False,
            "1h": False,
            "30m": False,
        })

        minutes_left = (event_time - now).total_seconds() / 60
        time_ist = event_time.astimezone(IST).strftime("%d-%m-%Y %I:%M %p")

        name = html.escape(clean(event.get("title")))
        country = html.escape(clean(event.get("country")))
        previous = html.escape(clean(event.get("previous")))
        forecast = html.escape(clean(event.get("forecast")))
        actual = html.escape(clean(event.get("actual")))

        details = (
            f"🌍 Country: {country}\n"
            f"📌 Event: <b>{name}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {previous}\n"
            f"🔮 Forecast: {forecast}\n"
            f"📈 Actual: {actual}\n"
            "Source: Forex Factory"
        )

        if not status["detected"]:
            send_telegram("🔴 <b>HIGH-IMPACT EVENT DETECTED</b>\n\n" + details)
            status["detected"] = True
            save_state(state)

        if 55 <= minutes_left <= 65 and not status["1h"]:
            send_telegram("⏰ <b>1-HOUR REMINDER</b>\n\n" + details)
            status["1h"] = True
            save_state(state)

        if 25 <= minutes_left <= 35 and not status["30m"]:
            send_telegram("⏰ <b>30-MINUTE REMINDER</b>\n\n" + details)
            status["30m"] = True
            save_state(state)

    save_state(state)
    print("Calendar check completed.")


if __name__ == "__main__":
    main()
