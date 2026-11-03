"""Entry point: initialise components and run one check. No business logic here."""
from datetime import datetime

import config as cfg
from alerts import process, select_upcoming
from calendar_service import fetch_calendar
from commands import handle_updates
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
    state = load_state()
    now = datetime.now(cfg.UTC)

    print("Current UTC:", now.isoformat())
    print("Resend detection mode:", cfg.RESEND_DETECTION)

    # ---- run ----
    if store:
        handle_updates(notifier, audience)   # answer /start, /level, /currencies ...
        store.save()                         # remember them even if the calendar fails
    calendar = fetch_calendar(now, notifier)
    if calendar is None:
        print("Could not fetch calendar this run. Exiting cleanly.")
        return
    print("Events received:", len(calendar))

    min_rank = audience.min_rank() if store else None
    upcoming = select_upcoming(calendar, now, min_rank=min_rank, state=state)
    print(f"Upcoming events (min impact: {cfg.ALERT_MIN_IMPACT}):", len(upcoming))

    process(upcoming, now, notifier, state, audience if store else None)
    if store:
        store.save()                         # e.g. someone who blocked the bot was removed
    maybe_send_summary(calendar, now, notifier, state)
    print("Calendar check completed.")


if __name__ == "__main__":
    main()
