from decimal import Decimal
from pathlib import Path

import pytest

from app.services.bank_statement import (
    CsvColumnMapping,
    UnsupportedStatementFormat,
    account_identifier_matches,
    guess_csv_mapping,
    parse_csv_statement,
    parse_statement_file,
    preview_csv,
)

_CSV_SAMPLE = (Path(__file__).parent / "fixtures" / "sample.csv").read_bytes()


def test_matches_real_iban_directly():
    assert account_identifier_matches("DE02120300000000202051", "DE02120300000000202051")


def test_matches_real_iban_case_and_space_insensitive():
    assert account_identifier_matches(" de02 1203 0000 0000 2020 51 ", "DE02120300000000202051")


def test_rejects_different_iban():
    assert not account_identifier_matches("DE02120300000000202051", "DE00999999990000000000")


def test_matches_legacy_blz_kontonummer_format():
    # Real-world case: a bank's MT940 :25: field still exports the pre-SEPA
    # BLZ/Kontonummer format instead of an IBAN (found 2026-09). IBAN here is
    # the Bundesbank's own well-known public documentation example, not a
    # real account — its BLZ (37040044) and Kontonummer (0532013000) are
    # sliced out of it the same way the code under test does.
    assert account_identifier_matches("37040044/0532013000", "DE89370400440532013000")


def test_matches_legacy_format_with_fields_swapped():
    assert account_identifier_matches("0532013000/37040044", "DE89370400440532013000")


def test_matches_legacy_format_without_leading_zeros_on_kontonummer():
    assert account_identifier_matches("37040044/532013000", "DE89370400440532013000")


def test_rejects_legacy_format_for_different_account():
    assert not account_identifier_matches("37040044/0532013000", "DE02120300000000202051")


def test_rejects_garbage_identifier():
    assert not account_identifier_matches("not-an-iban-or-blz", "DE02120300000000202051")


def test_legacy_comparison_only_implemented_for_standard_german_iban_layout():
    # Non-German or non-standard-layout IBANs: no BLZ/Kontonummer decomposition
    # is attempted, so a legacy-format identifier never spuriously matches.
    assert not account_identifier_matches("12030000/0000202051", "FR1420041010050500013M02606")


# ---------------------------------------------------------------------------
# MT940 — the mt-940 library's 5.0.0-compatible default prepends a structured
# :86:'s ?31 sub-field (the counterparty's IBAN) onto ?32's ?applicant_name
# instead of keeping it separate (library issue #132). Confirmed against a
# real export, 2026-09-12: counterparty_name came out as
# "DE89370400440532013000Erika Musterfrau" with counterparty_iban left None.
# ---------------------------------------------------------------------------

_MT940_WITH_STRUCTURED_COUNTERPARTY = (
    ":20:STARTUMSE\r\n"
    ":25:DE02120300000000202051\r\n"
    ":28C:1/1\r\n"
    ":60F:C260101EUR1000,00\r\n"
    ":61:2603010301C75,00NTRFNONREF\r\n"
    ":86:166?00SEPA GUTSCHRIFT?20EREF+E2E-SPENDE-001?21SVWZ+Spende"
    "?30GENODEF1XXX?31DE89370400440532013000?32Erika Musterfrau\r\n"
    ":62F:C260301EUR1075,00\r\n"
).encode("utf-8")


def test_mt940_separates_counterparty_iban_from_name():
    statement = parse_statement_file(_MT940_WITH_STRUCTURED_COUNTERPARTY)
    [line] = statement.lines
    assert line.counterparty_name == "Erika Musterfrau"
    assert line.counterparty_iban == "DE89370400440532013000"


# ---------------------------------------------------------------------------
# CSV import — no standard schema across banks, so column mapping is
# user-driven (preview_csv -> guess_csv_mapping -> parse_csv_statement)
# ---------------------------------------------------------------------------

def test_preview_csv_sniffs_semicolon_delimiter():
    delimiter, columns, sample_rows = preview_csv(_CSV_SAMPLE)
    assert delimiter == ";"
    assert "Buchungstag" in columns
    assert len(sample_rows) == 2


def test_preview_csv_respects_explicit_delimiter():
    content = b"a,b,c\n1,2,3\n"
    delimiter, columns, _ = preview_csv(content, delimiter=",")
    assert delimiter == ","
    assert columns == ["a", "b", "c"]


def test_preview_csv_rejects_empty_file():
    with pytest.raises(UnsupportedStatementFormat):
        preview_csv(b"")


def test_guess_csv_mapping_matches_real_bank_header():
    _, columns, _ = preview_csv(_CSV_SAMPLE)
    guess = guess_csv_mapping(columns)
    assert guess["own_iban_column"] == "IBAN Auftragskonto"
    assert guess["booking_date_column"] == "Buchungstag"
    assert guess["amount_column"] == "Betrag"
    assert guess["balance_after_column"] == "Saldo nach Buchung"
    assert guess["counterparty_iban_column"] == "IBAN Zahlungsbeteiligter"
    assert guess["counterparty_name_column"] == "Name Zahlungsbeteiligter"
    assert guess["purpose_column"] == "Verwendungszweck"
    # Mandatsreferenz identifies a recurring SEPA mandate, not one
    # transaction — must never be auto-guessed as the dedup bank reference.
    assert guess["bank_reference_column"] is None


def test_guess_csv_mapping_does_not_double_claim_columns():
    # "IBAN Auftragskonto" must go to own_iban_column, not also match as
    # counterparty_iban_column just because both contain "iban".
    _, columns, _ = preview_csv(_CSV_SAMPLE)
    guess = guess_csv_mapping(columns)
    assert guess["own_iban_column"] != guess["counterparty_iban_column"]


_SAMPLE_MAPPING = CsvColumnMapping(
    delimiter=";",
    booking_date_column="Buchungstag",
    amount_column="Betrag",
    purpose_column="Verwendungszweck",
    counterparty_name_column="Name Zahlungsbeteiligter",
    counterparty_iban_column="IBAN Zahlungsbeteiligter",
    own_iban_column="IBAN Auftragskonto",
    balance_after_column="Saldo nach Buchung",
)


def test_parse_csv_statement_extracts_lines_and_iban():
    parsed = parse_csv_statement(_CSV_SAMPLE, _SAMPLE_MAPPING)
    assert parsed.iban == "DE02120300000000202051"
    assert len(parsed.lines) == 2
    assert parsed.lines[0].amount == Decimal("50.00")
    assert parsed.lines[0].purpose_text == "Mitgliedsbeitrag 2026"
    assert parsed.lines[1].amount == Decimal("-30.00")


def test_parse_csv_statement_captures_closing_balance_from_last_row():
    parsed = parse_csv_statement(_CSV_SAMPLE, _SAMPLE_MAPPING)
    assert parsed.closing_balance == Decimal("1020.00")
    from datetime import date
    assert parsed.closing_balance_date == date(2026, 3, 2)


def test_parse_csv_statement_without_own_iban_column_leaves_iban_empty():
    mapping = CsvColumnMapping(delimiter=";", booking_date_column="Buchungstag", amount_column="Betrag")
    parsed = parse_csv_statement(_CSV_SAMPLE, mapping)
    assert parsed.iban == ""


def test_parse_csv_statement_dot_decimal_separator():
    content = b"Datum;Betrag\n01.03.2026;1234.56\n"
    mapping = CsvColumnMapping(delimiter=";", decimal_separator=".", booking_date_column="Datum", amount_column="Betrag")
    parsed = parse_csv_statement(content, mapping)
    assert parsed.lines[0].amount == Decimal("1234.56")


def test_parse_csv_statement_comma_decimal_with_thousands_dot():
    content = b"Datum;Betrag\n01.03.2026;1.234,56\n"
    mapping = CsvColumnMapping(delimiter=";", decimal_separator=",", booking_date_column="Datum", amount_column="Betrag")
    parsed = parse_csv_statement(content, mapping)
    assert parsed.lines[0].amount == Decimal("1234.56")


def test_parse_csv_statement_missing_required_column_raises():
    mapping = CsvColumnMapping(delimiter=";", booking_date_column="Nicht da", amount_column="Betrag")
    with pytest.raises(UnsupportedStatementFormat):
        parse_csv_statement(_CSV_SAMPLE, mapping)


def test_parse_csv_statement_bad_date_format_raises():
    mapping = CsvColumnMapping(
        delimiter=";", date_format="%Y-%m-%d", booking_date_column="Buchungstag", amount_column="Betrag",
    )
    with pytest.raises(UnsupportedStatementFormat):
        parse_csv_statement(_CSV_SAMPLE, mapping)


def test_parse_csv_statement_skips_blank_rows():
    content = b"Datum;Betrag\n01.03.2026;50,00\n;\n02.03.2026;-30,00\n"
    mapping = CsvColumnMapping(delimiter=";", booking_date_column="Datum", amount_column="Betrag")
    parsed = parse_csv_statement(content, mapping)
    assert len(parsed.lines) == 2
