"""Which source names (CALENDAR_SOURCES) map to which source classes.
To add a site: write its source file, import it here, add one line."""
from sources.finnhub import FinnhubSource
from sources.fmp import FmpSource
from sources.forex_factory import ForexFactorySource
from sources.trading_economics import TradingEconomicsSource

SOURCE_CLASSES = {
    "forex_factory": ForexFactorySource,
    "trading_economics": TradingEconomicsSource,
    "fmp": FmpSource,
    "finnhub": FinnhubSource,
}


def build_sources(names):
    """Source objects for the given names; unknown names are skipped with a note."""
    built = []
    for name in names:
        cls = SOURCE_CLASSES.get(name)
        if cls is None:
            print(f"Source '{name}' is unknown or not available yet, skipping.")
            continue
        built.append(cls())
    return built
