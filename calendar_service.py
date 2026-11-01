"""Get the calendar the alerts and summary work from.

With USE_DATABASE=true every enabled source is fetched, its events are saved to
the database, and the merged result is returned. If a source is down or late,
what the others (and earlier runs) stored still gets used. With USE_DATABASE
off (the default) this does exactly what the bot always did: one Forex Factory
fetch, nothing stored."""
from datetime import datetime, timedelta, timezone

import config as cfg
from merge import merge
from sources import ForexFactorySource
from sources.registry import build_sources
from storage import StorageError, open_storage

LOOKBACK_DAYS = 2   # keep recent events readable (the daily summary shows today's)
THROTTLED = "throttled"

# Minutes to wait between asks, per source. Sources with daily request limits
# are asked rarely; Forex Factory's free copy is asked on every run.
MIN_INTERVAL_MINUTES = {"trading_economics": 30, "fmp": 30, "finnhub": 30}


def interval_for(source):
    minutes = getattr(source, "min_interval_minutes",
                      MIN_INTERVAL_MINUTES.get(source.source_id, 0))
    return timedelta(minutes=minutes)


def sync(sources, storage, now=None):
    """Fetch every source that is due and store what it returns. One failing
    source never stops the others. Returns {source_id: events stored, None if
    unavailable, or "throttled" if it was asked too recently}."""
    now = now or datetime.now(timezone.utc)
    results = {}
    for source in sources:
        interval = interval_for(source)
        if interval:
            last = storage.last_attempt(source.source_id)
            if last is not None and now - last < interval:
                wait = int((interval - (now - last)).total_seconds() // 60) + 1
                print(f"{source.source_id}: asked recently, next check in ~{wait} min")
                results[source.source_id] = THROTTLED
                continue
            # recorded before asking, so a refused key is not retried every run
            storage.record_attempt(source.source_id, now)
        try:
            events = source.fetch_events()
        except Exception as e:  # a broken source must not break the run
            print(f"{source.source_id}: failed ({e})")
            events = None
        if events is None:
            print(f"{source.source_id}: unavailable this run")
            results[source.source_id] = None
            continue
        storage.upsert(events)
        results[source.source_id] = len(events)
        print(f"{source.source_id}: {len(events)} events")
    return results


def load_calendar(now, sources, storage, priority, keep_days, ahead_days):
    """Sync, tidy old rows, and return the merged calendar as feed-style dicts.
    None only when nothing could be fetched AND nothing is stored."""
    results = sync(sources, storage, now)
    storage.prune(now - timedelta(days=keep_days))
    stored = storage.events_between(now - timedelta(days=LOOKBACK_DAYS),
                                    now + timedelta(days=ahead_days))
    if results and all(v is None for v in results.values()):
        if not stored:
            return None
        print("No source reachable; using events stored by earlier runs.")
    print("Stored events by source:", storage.counts_by_source())
    return [e.to_calendar_dict() for e in merge(stored, tuple(priority))]


def fetch_calendar(now):
    """The calendar for this run (list of dicts), or None if there is none."""
    if not cfg.USE_DATABASE:
        return ForexFactorySource().fetch_calendar()
    try:
        storage = open_storage(cfg.DATABASE_URL)
    except StorageError as e:
        print(f"Database unavailable ({e}); falling back to a direct fetch.")
        return ForexFactorySource().fetch_calendar()
    with storage:
        return load_calendar(now, build_sources(cfg.CALENDAR_SOURCES), storage,
                             cfg.SOURCE_PRIORITY, cfg.DB_KEEP_DAYS, cfg.HORIZON_DAYS + 1)
