"""What every storage backend must offer. Nothing else in the bot touches a
database directly, so changing the backend means adding one file here."""
from abc import ABC, abstractmethod


class StorageError(Exception):
    pass


class Storage(ABC):
    @abstractmethod
    def upsert(self, events):
        """Insert or update events (one row per source + event key). Empty
        values never erase a value we already have. Returns how many were written."""

    @abstractmethod
    def events_between(self, start, end, sources=None):
        """Events with start <= time < end (aware datetimes), optionally only
        from the given sources, ordered by time. Returns Event objects."""

    @abstractmethod
    def prune(self, before):
        """Delete events older than `before`. Returns how many were removed."""

    @abstractmethod
    def counts_by_source(self):
        """{source: number of stored events}, for logs and diagnostics."""

    @abstractmethod
    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
