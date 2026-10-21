
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


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temporary, STATE_FILE)


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
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret."
        )

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

    print("Telegram accepted message; message_id:",
          result.get("result", {}).get("message_id"))


def telegram_test_once(state):
    # Send the diagnostic only once, then remember successful delivery.
    if state.get("_telegram_test_sent"):
        return

    send_telegram(
        "✅ <b>Economic Calendar Bot Test</b>\n"
        "Telegram delivery is working."
    )
    state["_telegram_test_sent"] = True
    save_state(state)


def main():
    now = datetime.now(UTC)
    end = now + timedelta(days=30)
    state = load_state()

    print("Source: Forex Factory weekly JSON")
    print("Current UTC:", now.isoformat())
    print("Configured window: next 30 days")

    # Sends a one-time test message. Remove this call after verifying delivery.
    telegram_test_once(state)

    calendar = fetch_calendar()
    print("Events received:", len(calendar))

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
    print("Note: this feed does not cover the full 30-day period.")

    for key, event, event_time in sorted(
        upcoming, key=lambda item: item[2]
    ):
        status = state.setdefault(key, {
            "detected": False,
            "1h": False,
            "30m": False,
        })

        minutes_left = (event_time - now).total_seconds() / 60

        print(
            "MATCH:", clean(event.get("title")),
            "| UTC:", event_time.isoformat(),
            "| IST:", event_time.astimezone(IST).isoformat(),
            "| saved status:", status,
        )

        time_ist = event_time.astimezone(IST).strftime(
            "%d-%m-%Y %I:%M %p"
        )

        details = (
            f"🌍 Country: {html.escape(clean(event.get('country')))}\n"
            f"📌 Event: <b>{html.escape(clean(event.get('title')))}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {html.escape(clean(event.get('previous')))}\n"
            f"🔮 Forecast: {html.escape(clean(event.get('forecast')))}\n"
            f"📈 Actual: {html.escape(clean(event.get('actual')))}\n"
            "Source: Forex Factory"
        )

        if not status.get("detected", False):
            print("Sending detection alert:", key)
            send_telegram(
                "🔴 <b>HIGH-IMPACT EVENT DETECTED</b>\n\n" + details
            )
            status["detected"] = True
            save_state(state)
        else:
            print("Detection alert skipped: already sent.")

        if 55 <= minutes_left <= 65 and not status.get("1h", False):
            print("Sending 1-hour reminder:", key)
            send_telegram("⏰ <b>1-HOUR REMINDER</b>\n\n" + details)
            status["1h"] = True
            save_state(state)
        elif not status.get("1h", False):
            print("1-hour reminder not due; minutes remaining:", round(minutes_left))

        if 25 <= minutes_left <= 35 and not status.get("30m", False):
            print("Sending 30-minute reminder:", key)
            send_telegram("⏰ <b>30-MINUTE REMINDER</b>\n\n" + details)
            status["30m"] = True
            save_state(state)
        elif not status.get("30m", False):
            print("30-minute reminder not due; minutes remaining:", round(minutes_left))

    save_state(state)
    print("Calendar check completed.")


if __name__ == "__main__":
    main()
