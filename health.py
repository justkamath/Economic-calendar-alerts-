"""Tell the owner when a data source has been down for hours (and when it's back).

Only the first chat ID in TELEGRAM_CHAT_ID (yours) is told, never the group or
friends. A source that has never worked (a refused or missing key) is only
logged, so a plan you simply don't have never nags you.
"""
from datetime import timedelta

DOWN_AFTER = timedelta(hours=3)   # how long without data before you are told
REFRESH = timedelta(minutes=30)   # how often "last worked" is re-saved (keeps commits few)

NAMES = {
    "forex_factory": "Forex Factory",
    "trading_economics": "Trading Economics",
    "fmp": "Financial Modeling Prep",
    "finnhub": "Finnhub",
    "biquote": "biquote",
}


def _name(source):
    return NAMES.get(source, source)


def _hours(delta):
    hours = int(delta.total_seconds() // 3600)
    return "about 1 hour" if hours <= 1 else f"about {hours} hours"


def down_message(source, since, now):
    return (f"⚠️ <b>{_name(source)} is not responding</b>\n"
            f"No data for {_hours(now - since)}.\n\n"
            "<i>Your alerts keep working from the other sources and earlier "
            "saved events. I'll tell you when it's back.</i>")


def back_message(source):
    return f"✅ <b>{_name(source)} is back</b>\n\n<i>Everything is running normally again.</i>"


def owner_notifier(notifier):
    """A function that sends one message to the owner only, or None."""
    if notifier is None or not getattr(notifier, "chat_ids", None):
        return None
    owner = notifier.chat_ids[0]

    def notify(message):
        notifier.send(message, chat_id=owner)
    return notify


def check(storage, results, now, notify=None):
    """Update each source's health from this run's results and send any notice.
    `results` is what calendar_service.sync returned. Never raises."""
    for source, outcome in results.items():
        try:
            if isinstance(outcome, bool) or not (outcome is None or isinstance(outcome, int)):
                continue                       # throttled: no news either way
            if outcome is None:
                _failed(storage, source, now, notify)
            else:
                _worked(storage, source, now, notify)
        except Exception as e:                 # health must never break alerts
            print(f"{source}: health check skipped ({e})")


def _failed(storage, source, now, notify):
    since = storage.last_success(source)
    if since is None:
        print(f"{source}: has not worked yet")
        return
    if now - since < DOWN_AFTER or storage.warned_at(source) is not None or not notify:
        return
    notify(down_message(source, since, now))   # if this raises, we try again next run
    storage.set_warned(source, now)
    print(f"{source}: down since {since.isoformat()}, owner told")


def _worked(storage, source, now, notify):
    was_down = storage.warned_at(source) is not None
    last = storage.last_success(source)
    if was_down and notify:
        notify(back_message(source))
        storage.set_warned(source, None)
        print(f"{source}: back, owner told")
    elif was_down:
        return                                 # keep the flag until we can tell the owner
    if last is None or now - last >= REFRESH or was_down:
        storage.record_success(source, now)
