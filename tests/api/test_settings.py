"""The secret values the trace redactor is given (F01-T10 AC3)."""

from tests.conftest import api_settings


def test_the_database_password_is_one_of_the_secret_values() -> None:
    settings = api_settings("postgresql://zorzo:p%40ss-word-123@localhost:5432/zorzo")
    secrets = settings.secret_values()
    assert (
        "p%40ss-word-123" in secrets and "p@ss-word-123" in secrets
    )  # as written, and as the database knows it
    assert settings.salt_seed.get_secret_value() in secrets


def test_a_url_without_a_password_adds_none() -> None:
    assert "" not in api_settings("postgresql://localhost:5432/zorzo").secret_values()


def test_the_database_password_in_the_query_string_is_one_of_the_secret_values() -> None:
    settings = api_settings("postgresql://localhost:5432/zorzo?sslmode=require&password=q%40ss-word-456")
    secrets = settings.secret_values()
    assert (
        "q%40ss-word-456" in secrets and "q@ss-word-456" in secrets
    )  # as written, and as the database knows it


def test_both_forms_are_secret_values_when_the_url_has_both() -> None:
    settings = api_settings("postgresql://zorzo:user-info-pass@localhost:5432/zorzo?password=query-pass")
    assert {"user-info-pass", "query-pass"} <= set(settings.secret_values())
