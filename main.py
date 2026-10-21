
import urllib.request
import urllib.parse
import json
import os
import html
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


# Economic calendar API
API_URL = "https://www.financecalendar.com/wp-json/fc/v1/calendar"

STATE_FILE = "sent_alerts.json"
IST = ZoneInfo("Asia/Kolkata")


def load_alerts():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_alerts(alerts):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(alerts, file, indent=2)


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({
            "is_disabled": True
        })
    }).encode()

    request = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": "EconomicCalendarBot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def get_event_time(event):
    """Parse an event timestamp and convert it to UTC."""
    time_text = (
        event.get("release_at_utc")
        or event.get("time_utc")
        or event.get("date")
    )

    if not time_text:
        return None

    try:
        event_time = datetime.fromisoformat(
            str(time_text).replace("Z", "+00:00")
        )

        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)

        return event_time.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def main():
    now = datetime.now(timezone.utc)

    # Request the next 30 days of calendar events.
    start_date = now.date()
    end_date = (now + timedelta(days=30)).date()

    alerts = load_alerts()

    # Set to true only when manually selected in GitHub Actions.
    force_resend = (
        os.environ.get("RESEND_ALERTS", "false").strip().lower()
        == "true"
    )

    print("Date range:", start_date, "to", end_date)
    print("Force resend enabled:", force_resend)

    params = urllib.parse.urlencode({
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "impact": "high"
    })

    request = urllib.request.Request(
        f"{API_URL}?{params}",
        headers={"User-Agent": "EconomicCalendarBot/1.0"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if isinstance(data, list):
        calendar = data
    elif isinstance(data, dict):
        calendar = data.get("events")
    else:
        calendar = None

    if not isinstance(calendar, list):
        raise ValueError(
            "Unexpected calendar response format. "
            "Check the API response."
        )

    print("Total events received:", len(calendar))

    high_events = [
        event for event in calendar
        if str(event.get("impact", "")).strip().lower() == "high"
    ]

    print("High-impact events:", len(high_events))

    for event in high_events:
        name = event.get("title") or event.get("name") or "Unnamed event"
        country = event.get("country") or "N/A"

        event_time = get_event_time(event)

        if event_time is None:
            print("Skipping event with missing/invalid time:", name)
            continue

        # Never send alerts for events that have already happened.
        if event_time <= now:
            continue

        event_id = event.get("id") or (
            f"{country}|{name}|{event_time.isoformat()}"
        )
        event_key = str(event_id)

        if event_key not in alerts:
            alerts[event_key] = {
                "detected": False,
                "1h": False,
                "30m": False
            }

        state = alerts[event_key]

        # Reset the state only for this manual resend run.
        if force_resend:
            state["detected"] = False
            state["1h"] = False
            state["30m"] = False

        time_ist = event_time.astimezone(IST).strftime(
            "%d-%m-%Y %I:%M %p"
        )

        safe_name = html.escape(str(name))
        safe_country = html.escape(str(country))

        previous = html.escape(str(
            event.get("prior")
            or event.get("previous")
            or "Not available"
        ))

        forecast = html.escape(str(
            event.get("consensus")
            or event.get("forecast")
            or "Not available"
        ))

        actual = html.escape(str(
            event.get("actual") or "Not available"
        ))

        details = (
            f"🌍 Country: {safe_country}\n"
            f"📌 Event: <b>{safe_name}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {previous}\n"
            f"🔮 Forecast: {forecast}\n"
            f"📈 Actual: {actual}\n"
        )

        source = (
            'Source: <a href="https://www.financecalendar.com/">'
            'Finance Calendar</a>'
        )

        minutes_left = (event_time - now).total_seconds() / 60

        # Send the detection alert if it has not been sent,
        # or if manual resend was selected.
        if not state.get("detected", False):
            send_telegram(
                "🔴 <b>HIGH-IMPACT ECONOMIC EVENT DETECTED</b>\n\n"
                f"{details}\n{source}"
            )

            state["detected"] = True
            save_alerts(alerts)

        # One-hour reminder.
        if (
            55 <= minutes_left <= 65
            and not state.get("1h", False)
        ):
            send_telegram(
                "⏰ <b>1-HOUR REMINDER</b>\n\n"
                f"{details}\n{source}"
            )

            state["1h"] = True
            save_alerts(alerts)

        # Thirty-minute reminder.
        if (
            25 <= minutes_left <= 35
            and not state.get("30m", False)
        ):
            send_telegram(
                "⏰ <b>30-MINUTE REMINDER</b>\n\n"
                f"{details}\n{source}"
            )

            state["30m"] = True
            save_alerts(alerts)

    save_alerts(alerts)
    print("Economic calendar checked successfully.")


if __name__ == "__main__":
    main()
