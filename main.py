
import urllib.request
import urllib.parse
import json
import os
import html
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

API_URL = "https://www.financecalendar.com/wp-json/fc/v1/calendar"
STATE_FILE = "sent_alerts.json"
IST = ZoneInfo("Asia/Kolkata")


def load_alerts():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_alerts(alerts):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(alerts, f, indent=2)


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
    value = (
        event.get("release_at_utc")
        or event.get("time_utc")
        or event.get("date")
    )

    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def main():
    now = datetime.now(timezone.utc)
    start_date = now.date()
    end_date = (now + timedelta(days=30)).date()

    alerts = load_alerts()

    # Manual resend OR automatic recovery after a failed run.
    manual_resend = (
        os.environ.get("RESEND_ALERTS", "false").lower() == "true"
    )
    recovery_pending = alerts.get("_recovery_pending", False)
    force_resend = manual_resend or recovery_pending

    print("Date range:", start_date, "to", end_date)
    print("Manual resend:", manual_resend)
    print("Recovery pending:", recovery_pending)
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
        raise ValueError("Unexpected calendar response format")

    high_events = [
        e for e in calendar
        if str(e.get("impact", "")).strip().lower() == "high"
    ]

    print("Total events received:", len(calendar))
    print("High-impact events:", len(high_events))

    for event in high_events:
        name = event.get("title") or event.get("name") or "Unnamed event"
        country = event.get("country") or "N/A"
        event_time = get_event_time(event)

        if event_time is None or event_time <= now:
            continue

        event_key = str(
            event.get("id")
            or f"{country}|{name}|{event_time.isoformat()}"
        )

        if event_key not in alerts:
            alerts[event_key] = {
                "detected": False,
                "1h": False,
                "30m": False
            }

        state = alerts[event_key]

        # Reset event flags only during a resend/recovery run.
        if force_resend:
            state["detected"] = False
            state["1h"] = False
            state["30m"] = False

        time_ist = event_time.astimezone(IST).strftime(
            "%d-%m-%Y %I:%M %p"
        )

        details = (
            f"🌍 Country: {html.escape(str(country))}\n"
            f"📌 Event: <b>{html.escape(str(name))}</b>\n"
            f"🕒 Time (IST): {time_ist}\n"
            f"📊 Previous: {html.escape(str(event.get('prior') or event.get('previous') or 'Not available'))}\n"
            f"🔮 Forecast: {html.escape(str(event.get('consensus') or event.get('forecast') or 'Not available'))}\n"
            f"📈 Actual: {html.escape(str(event.get('actual') or 'Not available'))}\n"
        )

        source = (
            'Source: <a href="https://www.financecalendar.com/">'
            'Finance Calendar</a>'
        )

        minutes_left = (event_time - now).total_seconds() / 60

        if not state.get("detected", False):
            send_telegram(
                "🔴 <b>HIGH-IMPACT ECONOMIC EVENT DETECTED</b>\n\n"
                f"{details}\n{source}"
            )
            state["detected"] = True
            save_alerts(alerts)

        if 55 <= minutes_left <= 65 and not state.get("1h", False):
            send_telegram(
                "⏰ <b>1-HOUR REMINDER</b>\n\n"
                f"{details}\n{source}"
            )
            state["1h"] = True
            save_alerts(alerts)

        if 25 <= minutes_left <= 35 and not state.get("30m", False):
            send_telegram(
                "⏰ <b>30-MINUTE REMINDER</b>\n\n"
                f"{details}\n{source}"
            )
            state["30m"] = True
            save_alerts(alerts)

    # Clear the recovery flag only after the entire run succeeds.
    alerts["_recovery_pending"] = False
    save_alerts(alerts)

    print("Economic calendar check completed successfully.")


if __name__ == "__main__":
    main()
