"""Entry point: initialise components and run one check. No business logic here."""
from datetime import datetime

import config as cfg
from alerts import process, select_upcoming
from calendar_service import fetch_calendar
from commands import handle_updates
from expiry_reminders import send_expiry_reminders
from expiry_sync import maybe_sync
from notifiers import TelegramNotifier
from state import load_state
from subscribers import build_audience
from summary import maybe_send_summary


def main():
    # ---- initialisation ----
    notifier = TelegramNotifier()
    notifier.validate()  # fail fast if secrets are missing
    audience = build_audience(notifier.chat_ids)
    store = audience.store
    enabled = store is not None   # (an empty list is still 'on')
    state = load_state()
    now = datetime.now(cfg.UTC)

    print("Current UTC:", now.isoformat())
    print("Resend detection mode:", cfg.RESEND_DETECTION)

    # ---- run ----
    try:
        maybe_sync(now, notifier)            # fresh exact expiry dates (at most once a day)
    except Exception as e:                   # never let this break the calendar run
        print("Expiry sync error:", type(e).__name__)
    if enabled:
        handle_updates(notifier, audience)   # answer /start, /level, /currencies ...
        store.save()                         # remember them even if the calendar fails
    calendar = fetch_calendar(now, notifier)
    if calendar is None:
        print("Could not fetch calendar this run. Exiting cleanly.")
        return
    print("Events received:", len(calendar))

    min_rank = audience.min_rank() if enabled else None
    upcoming = select_upcoming(calendar, now, min_rank=min_rank, state=state)
    print(f"Upcoming events (min impact: {cfg.ALERT_MIN_IMPACT}):", len(upcoming))

    process(upcoming, now, notifier, state, audience if enabled else None)
    if enabled:
        try:
            sent = send_expiry_reminders(notifier, audience, now)
            if sent:
                print("Expiry reminders sent:", sent)
        except Exception as e:               # never let reminders break the calendar run
            print("Expiry reminders failed:", type(e).__name__)
        store.save()                         # e.g. someone who blocked the bot was removed
    maybe_send_summary(calendar, now, notifier, state)
    print("Calendar check completed.")


if __name__ == "__main__":
    main()
