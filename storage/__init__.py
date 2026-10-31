"""Pick a storage backend from DATABASE_URL.

    sqlite:///data/calendar.db      a file next to the code (default)
    sqlite:////var/lib/bot/cal.db   an absolute path
    sqlite:///:memory:              throw-away, for tests

To move to a server database later (e.g. Postgres), add storage/postgres.py with
a class that implements storage.base.Storage and register its scheme below.
Nothing else in the bot needs to change."""
from storage.base import Storage, StorageError  # noqa: F401


def open_storage(url):
    scheme, sep, rest = (url or "").partition("://")
    if scheme == "sqlite" and sep:
        from storage.sqlite import SqliteStorage
        return SqliteStorage(rest[1:] if rest.startswith("/") else rest)
    raise StorageError(
        f"Unsupported DATABASE_URL {url!r}. Supported now: sqlite:///path/to/file.db")
