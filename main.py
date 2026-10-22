"""Entry point: initialise components and run one check. No business logic here."""
from datetime import datetime

import config as cfg
from alerts import process, select_upcoming
from notifiers import TelegramNotifier
from sources import ForexFactorySource
from state import load_state


def main():
    # ---- initialisation ----
    source = ForexFactorySource()
    notifier = TelegramNotifier()
    notifier.validate()  # fail fast if secrets are missing
    state = load_state()
    now = datetime.now(cfg.UTC)

    print("Current UTC:", now.isoformat())
    print("Resend detection mode:", cfg.RESEND_DETECTION)

    # ---- run ----
    calendar = source.fetch_calendar()
    if calendar is None:
        print("Could not fetch calendar this run. Exiting cleanly.")
        return
    print("Events received:", len(calendar))

    upcoming = select_upcoming(calendar, now)
    print(f"Upcoming events (min impact: {cfg.ALERT_MIN_IMPACT}):", len(upcoming))

    process(upcoming, now, notifier, state)
    print("Calendar check completed.")


if __name__ == "__main__":
    main()
