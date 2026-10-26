from decimal import Decimal

from app.config import settings
from app.services.formatting import format_decimal, format_money


def test_default_separators_are_german_style():
    assert format_decimal(Decimal("1234567.5")) == "1.234.567,50"
    assert format_money(Decimal("-12.3")) == "-12,30 €"
    assert format_money(Decimal("0")) == "0,00 €"


def test_configured_separators(monkeypatch):
    monkeypatch.setattr(settings, "DECIMAL_SEPARATOR", ".")
    monkeypatch.setattr(settings, "THOUSANDS_SEPARATOR", ",")
    assert format_decimal(Decimal("1234.5")) == "1,234.50"
    monkeypatch.setattr(settings, "THOUSANDS_SEPARATOR", "")
    assert format_money(Decimal("1234.5")) == "1234.50 €"


def test_api_stays_plain_numbers(treasurer_client):
    resp = treasurer_client.get("/api/v1/ledger/report/euer?year=2026")
    assert resp.status_code == 200
    assert resp.json()["total_income"] == "0.00"
