"""Small helpers shared by the API-key sources."""

KNOWN_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY"}
# Two-letter country codes -> the currency Forex Factory files them under.
# Only the economies Forex Factory covers, so the sources line up.
CURRENCY_BY_CODE = {
    "US": "USD",
    "EU": "EUR", "EMU": "EUR", "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR",
    "GB": "GBP", "UK": "GBP",
    "JP": "JPY",
    "AU": "AUD",
    "NZ": "NZD",
    "CA": "CAD",
    "CH": "CHF",
    "CN": "CNY",
}
IMPACT_WORDS = {"high": "high", "medium": "medium", "med": "medium", "low": "low"}


def currency_from(currency, country):
    """Covered currency code from a currency field or a country code; else None."""
    code = str(currency or "").strip().upper()
    if code in KNOWN_CURRENCIES:
        return code
    return CURRENCY_BY_CODE.get(str(country or "").strip().upper())


def impact_from(value, title=""):
    if "holiday" in str(title).lower():
        return "holiday"
    return IMPACT_WORDS.get(str(value or "").strip().lower(), "")


def value_text(number, unit=None):
    """0.3 + '%' -> '0.3%'; 198000.0 + 'K' stays as given; None stays None."""
    if number is None or number == "":
        return None
    if isinstance(number, (int, float)) and not isinstance(number, bool):
        text = f"{number:g}"
    else:
        text = str(number).strip()
    unit = str(unit or "").strip()
    return f"{text}{unit}" if unit and not text.endswith(unit) else text
