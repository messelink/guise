"""Per-username login backoff (issue #23)."""
from unittest.mock import MagicMock, patch

import pytest

from app import backoff
from app.backoff import BASE_DELAY, FREE_PER_PAIR, FREE_PER_USER, MAX_DELAY, LoginBackoff

API = "/api/alias/random/new"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def tracker(clock):
    return LoginBackoff(clock=clock)


def fail(tracker, n, user="alice", ip="1.1.1.1"):
    for _ in range(n):
        tracker.record_failure(user, ip)


class TestCurve:
    def test_free_failures_not_throttled(self, tracker):
        fail(tracker, FREE_PER_PAIR)
        assert tracker.retry_after("alice", "1.1.1.1") == 0

    def test_first_lock_is_base_delay(self, tracker):
        fail(tracker, FREE_PER_PAIR + 1)
        assert tracker.retry_after("alice", "1.1.1.1") == BASE_DELAY

    def test_doubles_per_failure(self, tracker):
        fail(tracker, FREE_PER_PAIR + 3)
        assert tracker.retry_after("alice", "1.1.1.1") == BASE_DELAY * 4

    def test_capped(self, tracker):
        fail(tracker, FREE_PER_PAIR + 30)
        assert tracker.retry_after("alice", "1.1.1.1") == MAX_DELAY

    def test_lock_runs_out(self, tracker, clock):
        fail(tracker, FREE_PER_PAIR + 1)
        clock.now += BASE_DELAY
        assert tracker.retry_after("alice", "1.1.1.1") == 0

    def test_expires_after_quiet_hour(self, tracker, clock):
        fail(tracker, FREE_PER_PAIR)
        clock.now += backoff.EXPIRE_AFTER + 1
        fail(tracker, 1)
        # The counter restarted, so one more failure is still free.
        assert tracker.retry_after("alice", "1.1.1.1") == 0


class TestKeys:
    def test_pair_lock_does_not_affect_other_ip(self, tracker):
        fail(tracker, FREE_PER_PAIR + 1, ip="6.6.6.6")
        assert tracker.retry_after("alice", "1.1.1.1") == 0

    def test_pair_lock_does_not_affect_other_user(self, tracker):
        fail(tracker, FREE_PER_PAIR + 1)
        assert tracker.retry_after("bob", "1.1.1.1") == 0

    def test_username_wide_lock_across_ips(self, tracker):
        # Spread over many IPs, each staying under the pair limit.
        for i in range(FREE_PER_USER + 1):
            tracker.record_failure("alice", f"10.0.0.{i}")
        assert tracker.retry_after("alice", "1.1.1.1") == BASE_DELAY

    def test_success_resets_pair_only(self, tracker):
        for i in range(FREE_PER_USER):
            tracker.record_failure("alice", f"10.0.0.{i}")
        fail(tracker, FREE_PER_PAIR)
        tracker.record_success("alice", "1.1.1.1")
        # The pair counter is back to zero (and 1.1.1.1 is now trusted)...
        assert tracker._pairs.entries.get(("alice", "1.1.1.1")) is None
        # ...but the user-wide counter still holds for any other client.
        assert tracker.retry_after("alice", "9.9.9.9") > 0

    def test_entries_bounded(self, tracker, monkeypatch):
        monkeypatch.setattr(backoff, "MAX_ENTRIES", 10)
        for i in range(50):
            tracker.record_failure(f"user{i}", "1.1.1.1")
        assert len(tracker._pairs.entries) <= 10

    def test_eviction_keeps_active_locks(self, tracker, monkeypatch):
        monkeypatch.setattr(backoff, "MAX_ENTRIES", 10)
        fail(tracker, FREE_PER_PAIR + 1, user="victim")
        # Spray fresh keys from rotating IPv4s to try to flush the victim's lock.
        for i in range(100):
            tracker.record_failure(f"spray{i}", f"10.1.{i}.1")
        assert tracker.retry_after("victim", "1.1.1.1") > 0

    def test_pair_spray_cannot_evict_user_lock(self, tracker, monkeypatch):
        monkeypatch.setattr(backoff, "MAX_ENTRIES", 10)
        for i in range(FREE_PER_USER + 1):
            tracker.record_failure("victim", f"10.0.0.{i}")
        for i in range(100):
            tracker.record_failure("gitea", f"10.2.{i}.1")
        assert tracker.retry_after("victim", "9.9.9.9") > 0

    def test_no_overflow_after_many_failures(self, tracker, clock):
        # 2**n on a float overflows past ~1024 doublings; the lock must hold.
        for _ in range(3000):
            clock.now += MAX_DELAY
            tracker.record_failure("alice", "1.1.1.1")
        assert tracker.retry_after("alice", "1.1.1.1") == MAX_DELAY

    def test_throttled_attempts_do_not_extend_lock(self, tracker, clock):
        fail(tracker, FREE_PER_PAIR + 1)
        for _ in range(50):
            assert tracker.begin("alice", "1.1.1.1") == "throttled"
        clock.now += BASE_DELAY
        assert tracker.retry_after("alice", "1.1.1.1") == 0

    def test_eviction_keeps_escalated_entry_between_locks(self, tracker, clock, monkeypatch):
        monkeypatch.setattr(backoff, "MAX_ENTRIES", 10)
        fail(tracker, FREE_PER_PAIR + 5, user="victim")
        clock.now += MAX_DELAY  # lock has run out, escalation remains
        for i in range(100):
            tracker.record_failure("gitea", f"10.3.{i}.1")
        assert ("victim", "1.1.1.1") in tracker._pairs.entries

    def test_trusted_table_bounded(self, tracker, monkeypatch):
        monkeypatch.setattr(backoff, "MAX_TRUSTED", 10)
        for i in range(50):
            tracker.record_success("alice", f"10.4.{i}.1")
        assert len(tracker._trusted) <= 10

    def test_ipv6_grouped_by_slash64(self, tracker):
        for i in range(FREE_PER_PAIR + 1):
            tracker.record_failure("alice", f"2001:db8:1:2::{i + 1:x}")
        assert tracker.retry_after("alice", "2001:db8:1:2:ffff::1") > 0
        assert tracker.retry_after("alice", "2001:db8:1:3::1") == 0


class TestTrustedClients:
    def _lock_user_wide(self, tracker):
        for i in range(FREE_PER_USER + 1):
            tracker.record_failure("alice", f"10.0.0.{i}")

    def test_trusted_client_bypasses_user_wide_lock(self, tracker):
        tracker.record_success("alice", "1.1.1.1")
        self._lock_user_wide(tracker)
        assert tracker.retry_after("alice", "1.1.1.1") == 0
        assert tracker.retry_after("alice", "9.9.9.9") > 0  # untrusted still locked

    def test_trusted_client_still_has_pair_lock(self, tracker):
        tracker.record_success("alice", "1.1.1.1")
        fail(tracker, FREE_PER_PAIR + 1)
        assert tracker.retry_after("alice", "1.1.1.1") == BASE_DELAY

    def test_trust_is_per_username(self, tracker):
        tracker.record_success("alice", "1.1.1.1")
        for i in range(FREE_PER_USER + 1):
            tracker.record_failure("bob", f"10.0.0.{i}")
        assert tracker.retry_after("bob", "1.1.1.1") > 0

    def test_trust_expires(self, tracker, clock):
        tracker.record_success("alice", "1.1.1.1")
        clock.now += backoff.TRUST_FOR + 1
        self._lock_user_wide(tracker)
        assert tracker.retry_after("alice", "1.1.1.1") > 0

    def test_trust_covers_ipv6_slash64(self, tracker):
        tracker.record_success("alice", "2001:db8:1:2::1")
        self._lock_user_wide(tracker)
        assert tracker.retry_after("alice", "2001:db8:1:2::99") == 0

    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_real_user_logs_in_during_distributed_attack(self, mock_imap, client):
        _imap(mock_imap, ok=True)
        assert _web_login(client, "alice", ip="1.1.1.1").status_code == 302
        _imap(mock_imap, ok=False)
        for i in range(FREE_PER_USER + 1):
            _api(client, "alice", ip=f"10.0.0.{i}")
        assert _api(client, "alice", ip="9.9.9.9").status_code == 429
        _imap(mock_imap, ok=True)
        assert _web_login(client, "alice", ip="1.1.1.1").status_code == 302


class TestInFlight:
    def test_second_concurrent_check_refused(self, tracker):
        assert tracker.begin("alice", "1.1.1.1") == "ok"
        assert tracker.begin("alice", "2.2.2.2") == "busy"
        tracker.record_failure("alice", "1.1.1.1")
        assert tracker.begin("alice", "2.2.2.2") == "ok"

    def test_other_users_not_blocked(self, tracker):
        assert tracker.begin("alice", "1.1.1.1") == "ok"
        assert tracker.begin("bob", "1.1.1.1") == "ok"

    def test_busy_response_is_distinct(self, client):
        tracker = client.application.extensions["guise_backoff"]
        assert tracker.begin("alice", "8.8.8.8") == "ok"  # a check still waiting on dovecot
        resp = _web_login(client, "alice")
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "1"
        assert b"in progress" in resp.data
        resp = _api(client, "alice")
        assert resp.status_code == 429 and resp.get_json() == {"error": "login in progress"}

    @patch("app.auth._imap_check", side_effect=RuntimeError("boom"))
    def test_claim_released_when_check_raises(self, _imap_check, app):
        from app import auth
        with app.test_request_context():
            with pytest.raises(RuntimeError):
                auth.check_credentials("alice", "pw", "1.1.1.1", app.config["GUISE"])  # NOSONAR — test fixture
            assert app.extensions["guise_backoff"].begin("alice", "1.1.1.1") == "ok"


def _imap(mock_imap, ok):
    import imaplib
    inst = MagicMock()
    inst.__enter__.return_value = inst
    if not ok:
        inst.login.side_effect = imaplib.IMAP4.error("LOGIN failed")
    mock_imap.return_value = inst


def _web_login(client, user, pw="pw", ip="1.1.1.1"):  # NOSONAR — test fixture, not a real credential
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    return client.post(
        "/login",
        data={"username": user, "password": pw, "csrf_token": "t"},
        environ_overrides={"REMOTE_ADDR": ip},
    )


def _api(client, user, pw="pw", ip="1.1.1.1"):  # NOSONAR — test fixture, not a real credential
    return client.post(API, headers={"Authentication": f"{user}:{pw}"}, environ_overrides={"REMOTE_ADDR": ip})


class TestWebLogin:
    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_throttled_after_free_failures_without_imap(self, mock_imap, client):
        _imap(mock_imap, ok=False)
        for _ in range(FREE_PER_PAIR + 1):
            assert _web_login(client, "alice").status_code == 200
        calls = mock_imap.call_count
        resp = _web_login(client, "alice")
        assert resp.status_code == 429
        assert b"Too many failed attempts" in resp.data
        assert int(resp.headers["Retry-After"]) == BASE_DELAY
        assert mock_imap.call_count == calls  # never reached dovecot

    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_correct_password_also_throttled(self, mock_imap, client):
        _imap(mock_imap, ok=False)
        for _ in range(FREE_PER_PAIR + 1):
            _web_login(client, "alice")
        _imap(mock_imap, ok=True)
        assert _web_login(client, "alice").status_code == 429

    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_real_user_on_other_ip_unaffected(self, mock_imap, client):
        _imap(mock_imap, ok=False)
        for _ in range(FREE_PER_PAIR + 1):
            _web_login(client, "alice", ip="6.6.6.6")
        _imap(mock_imap, ok=True)
        assert _web_login(client, "alice", ip="1.1.1.1").status_code == 302

    def test_denied_user_throttled_like_any_other(self, client):
        for _ in range(FREE_PER_PAIR + 1):
            _web_login(client, "gitea")
        assert _web_login(client, "gitea").status_code == 429


class TestApi:
    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_throttled_with_retry_after(self, mock_imap, client):
        _imap(mock_imap, ok=False)
        for _ in range(FREE_PER_PAIR + 1):
            assert _api(client, "alice").status_code == 401
        resp = _api(client, "alice")
        assert resp.status_code == 429
        assert resp.get_json() == {"error": "too many failed attempts"}
        assert int(resp.headers["Retry-After"]) == BASE_DELAY

    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_web_and_api_share_counters(self, mock_imap, client):
        _imap(mock_imap, ok=False)
        for _ in range(FREE_PER_PAIR + 1):
            _web_login(client, "alice")
        assert _api(client, "alice").status_code == 429

    def test_malformed_username_or_empty_password_not_counted(self, client):
        for _ in range(FREE_PER_PAIR + 5):
            assert _api(client, "Not Valid").status_code == 401
            assert _api(client, "alice", pw="").status_code == 401
        tracker = client.application.extensions["guise_backoff"]
        assert not tracker._pairs.entries and not tracker._users.entries
