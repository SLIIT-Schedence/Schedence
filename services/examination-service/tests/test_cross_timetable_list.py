"""LIST regression through the existing in-memory cross-cycle fixtures; no database."""
from fastapi.testclient import TestClient

from test_cross_timetable import admin, client, everything_booked, harness, identity, store, tokens  # noqa: F401
from test_saved_timetables import LIST


def test_list_protected_timetable_returns_valid_summary(client, admin, store):
    everything_booked(store)
    # Surface the underlying error instead of hiding it behind a JSON decoding failure.
    strict = TestClient(client.app, raise_server_exceptions=True)
    response = strict.get(LIST, headers=admin)
    assert response.status_code == 200
    assert response.json()["total"] == 1
