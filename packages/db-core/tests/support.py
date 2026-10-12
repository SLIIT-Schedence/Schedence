"""Shared fake credentials for the unit tests. None of these are real."""

FAKE_USER = "schedence_test_user"
FAKE_PASSWORD = "Sup3r-Secret-Pa55word"
REMOTE_URL = (
    f"postgresql://{FAKE_USER}:{FAKE_PASSWORD}@ep-example-123456-pooler.eu-central-1.aws.neon.tech"
    "/neondb?sslmode=require&channel_binding=require"
)
DIRECT_URL = (
    f"postgresql://{FAKE_USER}:{FAKE_PASSWORD}@ep-example-123456.eu-central-1.aws.neon.tech"
    "/neondb?sslmode=require"
)


def local_url(port: int) -> str:
    """A URL for a local server; local hosts may connect without TLS."""
    return f"postgresql://{FAKE_USER}:{FAKE_PASSWORD}@127.0.0.1:{port}/schedence_test"


def assert_no_secrets(text: str) -> None:
    assert FAKE_PASSWORD not in text
    assert REMOTE_URL not in text
    assert DIRECT_URL not in text
