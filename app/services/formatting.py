"""Display formatting of monetary amounts for documents and pages.

Only for human-readable output (PDF statements, Markdown export, web UI).
API responses keep plain machine-readable numbers.
"""
from decimal import Decimal
from typing import Optional

from app.config import settings


def format_decimal(amount: Decimal, thousands: bool = True) -> str:
    """`amount` with two decimals, using the configured decimal and
    thousands separators (`DECIMAL_SEPARATOR`, `THOUSANDS_SEPARATOR`)."""
    text = f"{Decimal(amount):,.2f}"  # always "1,234.56" before substitution
    whole, _, fraction = text.rpartition(".")
    whole = whole.replace(",", settings.THOUSANDS_SEPARATOR if thousands else "")
    return f"{whole}{settings.DECIMAL_SEPARATOR}{fraction}"


def format_money(amount: Decimal, currency: Optional[str] = None) -> str:
    """`format_decimal` followed by the currency symbol."""
    return f"{format_decimal(amount)} {settings.CURRENCY if currency is None else currency}"
