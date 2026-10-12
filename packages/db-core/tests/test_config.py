from __future__ import annotations

from pathlib import Path

import pytest
from support import DIRECT_URL, FAKE_PASSWORD, REMOTE_URL, assert_no_secrets

from schedence_db.config import (
    DatabaseSettings,
    find_env_file,
    load_settings,
    normalize_database_url,
)
from schedence_db.exceptions import DatabaseConfigurationError


def write_env(path: Path, *lines: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding=encoding)
    return path


# --- loading -----------------------------------------------------------------


def test_loads_from_environment_variables(remote_env: None) -> None:
    settings = load_settings(env_file=False)

    url = settings.get_url()
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "ep-example-123456-pooler.eu-central-1.aws.neon.tech"
    assert url.database == "neondb"
    assert url.query["sslmode"] == "require"
    assert url.query["channel_binding"] == "require"
    assert settings.app_env == "development"
    assert (settings.db_pool_size, settings.db_max_overflow) == (5, 5)


def test_loads_from_explicit_env_file(tmp_path: Path) -> None:
    env_file = write_env(
        tmp_path / "custom.env",
        f"DATABASE_URL={REMOTE_URL}",
        "DB_POOL_SIZE=3",
        "DB_POOL_TIMEOUT=12.5",
        "DB_ECHO=true",
        "UNRELATED_SETTING=ignored",
    )

    settings = load_settings(env_file)

    assert settings.get_url().password == FAKE_PASSWORD
    assert settings.db_pool_size == 3
    assert settings.db_pool_timeout == 12.5
    assert settings.db_echo is True


def test_discovers_nearest_env_file_from_a_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_env(tmp_path / ".env", f"DATABASE_URL={REMOTE_URL}")
    service_dir = tmp_path / "services" / "identity-service"
    service_dir.mkdir(parents=True)
    monkeypatch.chdir(service_dir)

    assert find_env_file() == tmp_path / ".env"
    assert load_settings().get_url().database == "neondb"


def test_service_env_file_takes_priority_over_repository_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_env(tmp_path / ".env", f"DATABASE_URL={REMOTE_URL}")
    service_dir = tmp_path / "services" / "identity-service"
    write_env(service_dir / ".env", f"DATABASE_URL={DIRECT_URL}")
    monkeypatch.chdir(service_dir)

    assert load_settings().get_url().host == "ep-example-123456.eu-central-1.aws.neon.tech"


def test_env_file_search_stops_at_repository_root(tmp_path: Path) -> None:
    write_env(tmp_path / "outside" / ".env", f"DATABASE_URL={REMOTE_URL}")
    (tmp_path / "outside" / "repo" / ".git").mkdir(parents=True)
    start = tmp_path / "outside" / "repo" / "services" / "x"
    start.mkdir(parents=True)

    assert find_env_file(start) is None


def test_env_file_variable_selects_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = write_env(tmp_path / "config" / "db.env", f"DATABASE_URL={REMOTE_URL}")
    monkeypatch.setenv("SCHEDENCE_ENV_FILE", str(env_file))

    assert load_settings().get_url().database == "neondb"


def test_missing_explicit_env_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DatabaseConfigurationError, match="Environment file not found"):
        load_settings(tmp_path / "missing.env")


def test_env_file_with_utf8_bom_is_read_correctly(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", f"DATABASE_URL={REMOTE_URL}", encoding="utf-8-sig")

    assert load_settings(env_file).get_url().database == "neondb"


def test_utf16_env_file_gives_a_clear_error(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", f"DATABASE_URL={REMOTE_URL}", encoding="utf-16")

    with pytest.raises(DatabaseConfigurationError, match="not UTF-8") as exc_info:
        load_settings(env_file)
    assert_no_secrets(str(exc_info.value))


def test_precedence_overrides_then_environment_then_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = write_env(tmp_path / ".env", f"DATABASE_URL={REMOTE_URL}", "DB_POOL_SIZE=2", "DB_MAX_OVERFLOW=2")
    monkeypatch.setenv("DB_POOL_SIZE", "4")

    settings = load_settings(env_file, db_max_overflow=9)

    assert settings.db_pool_size == 4  # environment beats .env
    assert settings.db_max_overflow == 9  # explicit override beats everything


def test_env_prefix_lets_a_service_use_its_own_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDENTITY_DATABASE_URL", DIRECT_URL)
    monkeypatch.setenv("IDENTITY_DB_POOL_SIZE", "2")

    settings = load_settings(env_file=False, env_prefix="IDENTITY_")

    assert settings.get_url().host == "ep-example-123456.eu-central-1.aws.neon.tech"
    assert settings.db_pool_size == 2


# --- missing / invalid configuration ------------------------------------------


def test_missing_database_url_is_reported_by_name() -> None:
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL is required"):
        load_settings(env_file=False)


def test_empty_database_url_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL is required"):
        load_settings(env_file=False)


def test_colon_instead_of_equals_in_env_file_gets_a_hint_without_leaking(tmp_path: Path) -> None:
    env_file = write_env(tmp_path / ".env", f"DATABASE_URL:{REMOTE_URL}")

    with pytest.raises(DatabaseConfigurationError) as exc_info:
        load_settings(env_file)

    message = str(exc_info.value)
    assert "DATABASE_URL is required" in message
    assert "equals sign" in message
    assert_no_secrets(message)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("not a url", "not a valid database URL"),
        (f"mysql://{'u'}:{FAKE_PASSWORD}@db.example.com/app", "scheme"),
        (f"postgresql+asyncpg://u:{FAKE_PASSWORD}@db.example.com/app", "scheme"),
        (f"postgresql+psycopg2://u:{FAKE_PASSWORD}@db.example.com/app", "scheme"),
        (f"postgresql://u:{FAKE_PASSWORD}@/app", "host"),
        (f"postgresql://u:{FAKE_PASSWORD}@db.example.com", "database name"),
        (f"postgresql://u:{FAKE_PASSWORD}@db.example.com:notaport/app", "not a valid database URL"),
        (f"postgresql://u:{FAKE_PASSWORD}@db.example.com/app?sslmode=disable", "sslmode=disable"),
        (f"postgresql://u:{FAKE_PASSWORD}@db.example.com/app?sslmode=prefer", "sslmode=prefer"),
    ],
)
def test_invalid_database_urls_are_rejected_without_leaking(
    url: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    with pytest.raises(DatabaseConfigurationError, match=expected) as exc_info:
        load_settings(env_file=False)

    assert "DATABASE_URL" in str(exc_info.value)
    assert_no_secrets(str(exc_info.value))
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_invalid_direct_url_is_reported_by_name(remote_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIRECT_DATABASE_URL", "nonsense")
    with pytest.raises(DatabaseConfigurationError, match="DIRECT_DATABASE_URL"):
        load_settings(env_file=False)


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        ("DB_POOL_SIZE", "0", "DB_POOL_SIZE"),
        ("DB_POOL_SIZE", "lots", "DB_POOL_SIZE"),
        ("DB_MAX_OVERFLOW", "-1", "DB_MAX_OVERFLOW"),
        ("DB_POOL_TIMEOUT", "0", "DB_POOL_TIMEOUT"),
        ("DB_CONNECT_TIMEOUT", "0", "DB_CONNECT_TIMEOUT"),
        ("DB_ECHO", "maybe", "DB_ECHO"),
        ("APP_ENV", "staging", "APP_ENV"),
    ],
)
def test_invalid_numeric_and_choice_settings(
    variable: str, value: str, expected: str, remote_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(DatabaseConfigurationError, match=expected) as exc_info:
        load_settings(env_file=False)
    assert_no_secrets(str(exc_info.value))


# --- URL normalisation ------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_supported_schemes_are_adapted_to_psycopg(scheme: str) -> None:
    normalized = normalize_database_url(f"{scheme}://u:p@db.example.com/app?sslmode=require")
    assert normalized.startswith("postgresql+psycopg://")


def test_sslmode_is_added_for_remote_hosts_and_other_parameters_kept() -> None:
    normalized = normalize_database_url("postgresql://u:p@db.example.com/app?channel_binding=require")
    assert "sslmode=require" in normalized
    assert "channel_binding=require" in normalized


def test_stronger_sslmodes_are_kept() -> None:
    normalized = normalize_database_url("postgresql://u:p@db.example.com/app?sslmode=verify-full")
    assert "sslmode=verify-full" in normalized


def test_local_database_may_connect_without_tls() -> None:
    normalized = normalize_database_url("postgresql://u:p@localhost:5432/app")
    assert "sslmode" not in normalized


def test_url_encoded_password_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    # Password "p@ss:w/rd%" URL-encoded.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p%40ss%3Aw%2Frd%25@db.example.com/app")
    assert load_settings(env_file=False).get_url().password == "p@ss:w/rd%"


# --- environments and secrets ------------------------------------------------------


def test_testing_environment_disables_client_pooling(remote_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    assert load_settings(env_file=False).use_null_pool is True


def test_explicit_null_pool_setting_wins(remote_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_USE_NULL_POOL", "false")
    assert load_settings(env_file=False).use_null_pool is False


def test_production_forbids_sql_echo(remote_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DB_ECHO", "true")
    with pytest.raises(DatabaseConfigurationError, match="DB_ECHO must be false in production"):
        load_settings(env_file=False)


def test_direct_url_falls_back_to_pooled_url(remote_env: None) -> None:
    settings = load_settings(env_file=False)
    assert settings.has_direct_url is False
    assert settings.get_url(direct=True) == settings.get_url()


def test_direct_url_is_used_when_configured(remote_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIRECT_DATABASE_URL", DIRECT_URL)
    settings = load_settings(env_file=False)
    assert settings.get_url(direct=True).host == "ep-example-123456.eu-central-1.aws.neon.tech"
    assert settings.get_url().host == "ep-example-123456-pooler.eu-central-1.aws.neon.tech"


def test_settings_never_reveal_secrets_when_printed(remote_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIRECT_DATABASE_URL", DIRECT_URL)
    settings = load_settings(env_file=False)

    for rendered in (repr(settings), str(settings), str(settings.model_dump()), repr(settings.get_url())):
        assert_no_secrets(rendered)


def test_settings_are_immutable(remote_env: None) -> None:
    settings = load_settings(env_file=False)
    with pytest.raises(ValueError):
        settings.db_pool_size = 50  # type: ignore[misc]


def test_env_example_template_is_valid() -> None:
    template = Path(__file__).resolve().parents[3] / ".env.example"  # repository root
    settings = load_settings(template)
    assert settings.get_url().password == "PASSWORD"  # placeholders only
    assert settings.has_direct_url


def test_settings_can_be_constructed_directly() -> None:
    settings = DatabaseSettings(database_url=REMOTE_URL, _env_file=None)
    assert settings.get_url().drivername == "postgresql+psycopg"
