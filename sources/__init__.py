"""Calendar data sources. Each source exposes fetch_events() -> list[Event] | None."""
from sources.base import CalendarSource, events_from_calendar
from sources.finnhub import FinnhubSource
from sources.fmp import FmpSource
from sources.forex_factory import ForexFactorySource
from sources.trading_economics import TradingEconomicsSource

__all__ = ["CalendarSource", "FinnhubSource", "FmpSource", "ForexFactorySource",
           "TradingEconomicsSource", "events_from_calendar"]
