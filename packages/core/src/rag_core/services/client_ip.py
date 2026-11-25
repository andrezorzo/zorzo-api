"""The visitor's IP, read once and turned into a daily HMAC (LLD "Security", client IP).

The raw IP lives only inside these functions and the request that calls them: it is never stored, logged or
sent to a provider.
"""

import hashlib
import hmac
import ipaddress
from datetime import date

import structlog

from rag_core.errors import ApiError, ErrorCode

log = structlog.get_logger()

# `ip:port`, set by CloudFront; the origin secret keeps anyone else from reaching the API with it.
VIEWER_HEADER = "cloudfront-viewer-address"


def viewer_ip(header: str | None, peer: str | None, app_env: str) -> str:
    """The visitor's address: from CloudFront's header, or, only with APP_ENV=local, the socket peer.

    In production a missing header means every visitor would share the proxy's bucket, so the request is
    refused (`unavailable`) instead; a malformed header is refused anywhere, since CloudFront never sends one.
    """
    if header:
        address = header.rpartition(":")[0]  # IPv6 comes without brackets: the port is after the last colon
        address = address.partition("%")[0]  # an IPv6 scope (`%eth0`) names a local interface, not a visitor
        try:
            return str(ipaddress.ip_address(address))
        except ValueError:
            pass
    elif app_env == "local":
        return peer or "unknown"
    log.error("viewer_address_missing", present=bool(header))
    raise ApiError(ErrorCode.UNAVAILABLE)


def hashed_ip(ip: str, seed: bytes, day: date) -> str:
    """HMAC-SHA256(daily_salt, ip), with daily_salt = HMAC-SHA256(seed, utc_date); IPv6 by its /64 prefix.

    One subscriber usually holds a whole IPv6 /64, so the full address would let one visitor rotate past the
    limit. A peer that isn't an address (a local test client) is hashed as it is.
    """
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip)
    except ValueError:
        key = ip
    else:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped  # an IPv4 visitor in IPv6 form: its /64 would be everyone's
        prefix = 64 if address.version == 6 else 32
        key = str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
    salt = hmac.new(seed, day.isoformat().encode(), hashlib.sha256).digest()
    return hmac.new(salt, key.encode(), hashlib.sha256).hexdigest()
