"""Calendar data sources. Each source exposes fetch_calendar() -> list | None."""
from sources.forex_factory import ForexFactorySource

__all__ = ["ForexFactorySource"]
