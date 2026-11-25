"""AC6: Turnstile's real `siteverify` answers the test secrets as the suite's stand-in does.

Marked `network`: it calls Cloudflare, so it runs in the full verification, not in every test run.
"""

import pytest
from rag_core.adapters.turnstile import TurnstileBotCheck

from tests.fakes.turnstile import DUMMY_TOKEN, FAIL_SECRET, PASS_SECRET

pytestmark = pytest.mark.network


@pytest.mark.parametrize(
    ("secret", "token", "accepted"),
    [
        (PASS_SECRET, DUMMY_TOKEN, True),
        (FAIL_SECRET, DUMMY_TOKEN, False),
        (PASS_SECRET, "any-other-token", True),
        (FAIL_SECRET, "any-other-token", False),
    ],
)
async def test_the_test_secrets_behave_as_documented(secret: str, token: str, accepted: bool) -> None:
    check = TurnstileBotCheck(secret, timeout=10)
    try:
        assert await check.verify(token) is accepted
    finally:
        await check.close()
