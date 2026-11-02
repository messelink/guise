"""Rate-limit client key: IPv6 clients are grouped by /64 (issue #29)."""
from app.backoff import client_net

API = "/api/alias/random/new"
LIMIT = 30  # @limiter.limit("30 per minute") on the API


def _post(client, ip):
    # No credentials: the request is counted by the limiter and then 401s,
    # without reaching IMAP or docker.
    return client.post(API, environ_overrides={"REMOTE_ADDR": ip})


class TestClientNet:
    def test_ipv4_unchanged(self):
        assert client_net("203.0.113.5") == "203.0.113.5"

    def test_ipv6_grouped_by_64(self):
        assert client_net("2001:db8::1") == client_net("2001:db8::ffff:1") == "2001:db8::/64"

    def test_ipv4_mapped(self):
        assert client_net("::ffff:203.0.113.5") == "203.0.113.5"


class TestLimiterKey:
    def test_rotating_within_64_shares_limit(self, client):
        for i in range(1, LIMIT + 1):
            assert _post(client, f"2001:db8::{i:x}").status_code == 401
        assert _post(client, "2001:db8::beef").status_code == 429

    def test_other_64_not_affected(self, client):
        for i in range(1, LIMIT + 1):
            _post(client, f"2001:db8::{i:x}")
        assert _post(client, "2001:db8:0:1::1").status_code == 401

    def test_ipv4_addresses_limited_separately(self, client):
        for _ in range(LIMIT):
            _post(client, "203.0.113.5")
        assert _post(client, "203.0.113.5").status_code == 429
        assert _post(client, "203.0.113.6").status_code == 401
