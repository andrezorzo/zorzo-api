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
