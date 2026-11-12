"""Format a numeric amount as a locale-style currency string."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Union


def format_currency(
    amount: Union[int, float, Decimal],
    currency_code: str = "USD",
    symbol: bool = True,
    decimal_places: int = 2,
) -> str:
    """Return `amount` formatted as a currency string.

    `currency_code` is appended at the end unless `symbol` is True and a symbol
    for the currency is known, in which case the symbol is prepended.
    """
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
    value = Decimal(str(amount))
    quant = Decimal(10) ** -decimal_places
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)

    parts = f"{rounded:,.{decimal_places}f}"
    if symbol and currency_code in symbols:
        return f"{symbols[currency_code]}{parts}"
    return f"{parts} {currency_code}"


def parse_currency(raw: str) -> Decimal:
    """Parse a currency string back into a Decimal.

    Strips known currency symbols and commas, then converts the remainder.
    """
    cleaned = raw.replace(",", "").replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
    cleaned = cleaned.strip().split()[0]
    return Decimal(cleaned)
