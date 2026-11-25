"""AC4: the visitor's IP turns into a daily HMAC as it enters the app (LLD "Security", client IP)."""

from datetime import date

import pytest
from rag_core.errors import ApiError, ErrorCode
from rag_core.services.client_ip import hashed_ip, viewer_ip

SEED = b"s" * 32


def test_reads_the_cloudfront_viewer_address() -> None:
    assert viewer_ip("203.0.113.7:4431", "127.0.0.1", "production") == "203.0.113.7"
    # IPv6 comes without brackets: the port is after the last colon.
    assert viewer_ip("2001:db8:85a3::8a2e:370:7334:46532", "127.0.0.1", "production") == (
        "2001:db8:85a3::8a2e:370:7334"
    )
    assert viewer_ip("fe80::1%eth0:46532", "127.0.0.1", "production") == "fe80::1"


def test_falls_back_to_the_socket_only_locally() -> None:
    assert viewer_ip(None, "192.0.2.1", "local") == "192.0.2.1"
    for header in (None, "not an address"):
        with pytest.raises(ApiError) as exc:
            viewer_ip(header, "127.0.0.1", "production")
        assert exc.value.code is ErrorCode.UNAVAILABLE


def test_the_hash_changes_with_the_utc_day() -> None:
    day = date(2026, 11, 25)
    first = hashed_ip("203.0.113.7", SEED, day)
    assert first == hashed_ip("203.0.113.7", SEED, day)
    assert "203.0.113.7" not in first
    assert first != hashed_ip("203.0.113.7", SEED, date(2026, 11, 26))
    assert first != hashed_ip("203.0.113.8", SEED, day)


def test_ipv6_is_hashed_by_its_64_prefix() -> None:
    day = date(2026, 11, 25)
    assert hashed_ip("2001:db8:1:2::1", SEED, day) == hashed_ip("2001:db8:1:2:ffff::9", SEED, day)
    assert hashed_ip("2001:db8:1:2::1", SEED, day) != hashed_ip("2001:db8:1:3::1", SEED, day)


def test_an_ipv4_mapped_address_is_hashed_as_ipv4() -> None:
    day = date(2026, 11, 25)
    assert hashed_ip("::ffff:203.0.113.7", SEED, day) == hashed_ip("203.0.113.7", SEED, day)
    assert hashed_ip("::ffff:203.0.113.7", SEED, day) != hashed_ip("::ffff:203.0.113.8", SEED, day)
