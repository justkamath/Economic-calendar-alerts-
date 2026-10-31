"""SQLite backend: one file, no server, built into Python."""
import os
import sqlite3
from datetime import datetime, timezone

from models import Event, to_utc
from storage.base import Storage, StorageError

_STAMP = "%Y-%m-%dT%H:%M:%S+00:00"   # fixed width, so text order == time order

# Schema versions: add a new step here and bump nothing else.
_MIGRATIONS = (
    """
    CREATE TABLE events (
        source     TEXT NOT NULL,
        key        TEXT NOT NULL,
        title      TEXT NOT NULL,
        country    TEXT NOT NULL,
        time_utc   TEXT NOT NULL,
        impact     TEXT NOT NULL DEFAULT '',
        previous   TEXT,
        forecast   TEXT,
        actual     TEXT,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (source, key)
    );
    CREATE INDEX events_time ON events (time_utc);
    """,
)


def _stamp(value):
    return to_utc(value).strftime(_STAMP)


class SqliteStorage(Storage):
    def __init__(self, path):
        self.path = path
        try:
            if path != ":memory:":
                folder = os.path.dirname(path)
                if folder:
                    os.makedirs(folder, exist_ok=True)
            self._db = sqlite3.connect(path)
            self._db.row_factory = sqlite3.Row
            self._migrate()
        except (sqlite3.Error, OSError) as e:
            raise StorageError(f"Cannot open database {path!r}: {e}") from e

    def _migrate(self):
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        for number in range(version, len(_MIGRATIONS)):
            with self._db:
                self._db.executescript(_MIGRATIONS[number])
                self._db.execute(f"PRAGMA user_version = {number + 1}")

    def upsert(self, events):
        now = datetime.now(timezone.utc).strftime(_STAMP)
        rows = [(e.source, e.key, e.title, e.country, _stamp(e.time), e.impact,
                 e.previous, e.forecast, e.actual, now) for e in events]
        with self._db:
            self._db.executemany(
                """
                INSERT INTO events (source, key, title, country, time_utc, impact,
                                    previous, forecast, actual, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source, key) DO UPDATE SET
                    impact     = CASE WHEN excluded.impact != ''
                                      THEN excluded.impact ELSE events.impact END,
                    previous   = COALESCE(excluded.previous, events.previous),
                    forecast   = COALESCE(excluded.forecast, events.forecast),
                    actual     = COALESCE(excluded.actual, events.actual),
                    fetched_at = excluded.fetched_at
                """, rows)
        return len(rows)

    def events_between(self, start, end, sources=None):
        sql = "SELECT * FROM events WHERE time_utc >= ? AND time_utc < ?"
        args = [_stamp(start), _stamp(end)]
        if sources:
            sql += f" AND source IN ({','.join('?' * len(sources))})"
            args += list(sources)
        sql += " ORDER BY time_utc, source, title"
        return [Event(source=r["source"], title=r["title"], country=r["country"],
                      time=r["time_utc"], impact=r["impact"], previous=r["previous"],
                      forecast=r["forecast"], actual=r["actual"])
                for r in self._db.execute(sql, args)]

    def prune(self, before):
        with self._db:
            cur = self._db.execute("DELETE FROM events WHERE time_utc < ?",
                                   (_stamp(before),))
        return cur.rowcount

    def counts_by_source(self):
        return {r["source"]: r["n"] for r in self._db.execute(
            "SELECT source, COUNT(*) AS n FROM events GROUP BY source")}

    def close(self):
        self._db.close()
