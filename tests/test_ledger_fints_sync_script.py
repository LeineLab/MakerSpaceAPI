"""scripts/ledger_fints_sync.py's default_date_range() deliberately mirrors
fintsDefaultDatesForAccount() in app/web/templates/ledger/index.html by hand
(one's JS, one's Python — no shared implementation) — see Key Design
Decision #51/#52. A silent drift between the two would reintroduce exactly
the guaranteed-duplicate-import bug #51 fixed, so this locks the Python side
down against the same expected values the JS side was verified against live
(Europe/Berlin, "today" = 2026-09-15 at verification time)."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ledger_fints_sync", Path(__file__).resolve().parent.parent / "scripts" / "ledger_fints_sync.py"
)
sync_script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_script)


def test_default_date_range_tracks_last_transaction_date():
    today = date.today()
    expected_to = (today - timedelta(days=3)).isoformat()
    date_from, date_to = sync_script.default_date_range("2026-01-01")
    assert date_from == "2026-01-02"
    assert date_to == expected_to


def test_default_date_range_falls_back_to_30_days_without_history():
    date_from, date_to = sync_script.default_date_range(None)
    assert date.fromisoformat(date_to) - date.fromisoformat(date_from) == timedelta(days=30)


def test_default_date_range_clamps_from_to_not_exceed_to():
    """A last_transaction_date within the last 3 days (e.g. a manual import
    ran very recently) would otherwise push date_from past date_to —
    clamped instead of producing an inverted range."""
    today = date.today()
    recent = (today - timedelta(days=1)).isoformat()
    date_from, date_to = sync_script.default_date_range(recent)
    assert date_from == date_to


@pytest.mark.parametrize("days_ago", [0, 1, 2, 3, 5, 10, 100])
def test_default_date_range_never_produces_inverted_range(days_ago):
    last = (date.today() - timedelta(days=days_ago)).isoformat()
    date_from, date_to = sync_script.default_date_range(last)
    assert date_from <= date_to
