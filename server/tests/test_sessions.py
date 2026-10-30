"""Server-side session registry and revocation (issue #26)."""
import dataclasses
import logging
from unittest.mock import MagicMock, patch

import pytest

from app import sessions
from app.sessions import SESSION_LIFETIME, SessionRegistry

HOUR = 60 * 60.0


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def registry(clock):
    return SessionRegistry(clock=clock)


@pytest.fixture
def app_clock(app, clock):
    """Swap the app's registry for one on the fake clock."""
    app.extensions["guise_sessions"] = SessionRegistry(clock=clock)
    return clock


def _result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _login(client, user="alice"):
    with patch("app.auth.imaplib.IMAP4_SSL") as mock_imap:
        inst = MagicMock()
        inst.__enter__.return_value = inst
        mock_imap.return_value = inst
        with client.session_transaction() as s:
            s["csrf_token"] = "t"
        resp = client.post("/login", data={"username": user, "password": "pw", "csrf_token": "t"})  # NOSONAR — test fixture, not a real credential
    assert resp.status_code == 302
    return resp


def _dashboard(client):
    with patch("app.aliases._run_setup", return_value=_result(stdout="")):
        return client.get("/")


def _logged_in(resp):
    return resp.status_code == 200


def _kicked_out(resp):
    return resp.status_code == 302 and "/login" in resp.headers["Location"]


def _copy_cookie(src, dst):
    dst.set_cookie("session", src.get_cookie("session").value)


def _rejections(caplog):
    return [r.message for r in caplog.records if "SESSION_REJECTED" in r.message]


class TestRegistry:
    def test_create_then_check(self, registry):
        sid = registry.create("alice")
        assert registry.check(sid, "alice")

    def test_sids_are_unique_and_long(self, registry):
        sids = {registry.create("alice") for _ in range(100)}
        assert len(sids) == 100
        assert all(len(s) >= 43 for s in sids)

    def test_unknown_sid(self, registry):
        assert registry.status("nope", "alice") == "unknown"

    def test_wrong_user(self, registry):
        sid = registry.create("alice")
        assert registry.status(sid, "bob") == "unknown"
        # A mismatch doesn't burn the real owner's session.
        assert registry.check(sid, "alice")

    def test_non_string_inputs(self, registry):
        sid = registry.create("alice")
        assert registry.status(None, "alice") == "unknown"
        assert registry.status(sid, None) == "unknown"
        assert registry.status(["x"], "alice") == "unknown"

    def test_expires_at_lifetime(self, registry, clock):
        sid = registry.create("alice")
        clock.now += SESSION_LIFETIME - 1
        assert registry.check(sid, "alice")
        clock.now += 1
        assert registry.status(sid, "alice") == "expired"
        # Dropped once seen expired.
        assert registry.status(sid, "alice") == "unknown"

    def test_revoke(self, registry):
        sid = registry.create("alice")
        registry.revoke(sid)
        assert not registry.check(sid, "alice")
        registry.revoke(sid)  # idempotent
        registry.revoke(None)

    def test_revoke_user(self, registry):
        a1, a2, b = registry.create("alice"), registry.create("alice"), registry.create("bob")
        registry.revoke_user("alice")
        assert not registry.check(a1, "alice")
        assert not registry.check(a2, "alice")
        assert registry.check(b, "bob")

    def test_cap_evicts_oldest(self, registry, monkeypatch):
        monkeypatch.setattr(sessions, "MAX_SESSIONS", 3)
        sids = [registry.create(f"u{i}") for i in range(5)]
        assert [registry.check(s, f"u{i}") for i, s in enumerate(sids)] == [
            False, False, True, True, True,
        ]
        assert len(registry._sessions) == 3

    def test_per_user_cap_evicts_own_oldest(self, registry, monkeypatch):
        monkeypatch.setattr(sessions, "MAX_PER_USER", 3)
        bob = registry.create("bob")
        mine = [registry.create("alice") for _ in range(5)]
        assert [registry.check(s, "alice") for s in mine] == [False, False, True, True, True]
        assert registry.check(bob, "bob")

    def test_login_flood_cannot_evict_other_users(self, registry, monkeypatch):
        # One account logging in over and over (dropping its cookie each
        # time) must not push other users out of a full registry.
        monkeypatch.setattr(sessions, "MAX_SESSIONS", 50)
        monkeypatch.setattr(sessions, "MAX_PER_USER", 5)
        alice = registry.create("alice")
        for _ in range(1000):
            registry.create("mallory")
        assert registry.check(alice, "alice")
        assert len(registry._sessions) == 6

    def test_default_clock_counts_suspend(self):
        import time
        if hasattr(time, "CLOCK_BOOTTIME"):
            assert sessions._DEFAULT_CLOCK is sessions._boot_clock
        else:
            assert sessions._DEFAULT_CLOCK is time.monotonic

    def test_create_drops_expired(self, registry, clock):
        for i in range(5):
            registry.create(f"u{i}")
        clock.now += SESSION_LIFETIME
        fresh = registry.create("alice")
        assert list(registry._sessions) == [fresh]


class TestWebSessions:
    def test_login_dashboard_create_flow(self, client):
        _login(client)
        assert _logged_in(_dashboard(client))
        with client.session_transaction() as s:
            s["csrf_token"] = "t"
        with patch("app.aliases._run_setup", side_effect=[_result(stdout=""), _result()]) as mock_run:
            resp = client.post("/aliases", data={"label": "shop", "csrf_token": "t"})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"
        add_args = mock_run.call_args_list[1].args
        assert add_args[1:3] == ("alias", "add")
        assert add_args[4] == "alice@example.com"

    def test_login_sets_sid(self, client, app):
        _login(client)
        with client.session_transaction() as s:
            assert app.extensions["guise_sessions"].check(s["sid"], "alice")

    def test_copied_cookie_dies_on_logout(self, app, client):
        _login(client)
        thief = app.test_client()
        _copy_cookie(client, thief)
        assert _logged_in(_dashboard(thief))
        with client.session_transaction() as s:
            s["csrf_token"] = "t"
        client.post("/logout", data={"csrf_token": "t"})
        assert _kicked_out(_dashboard(thief))
        assert _kicked_out(_dashboard(client))

    def test_relogin_revokes_previous_session(self, app, client):
        _login(client)
        thief = app.test_client()
        _copy_cookie(client, thief)
        _login(client)
        assert _kicked_out(_dashboard(thief))
        assert _logged_in(_dashboard(client))

    def test_lifetime_holds_despite_activity(self, client, app_clock):
        _login(client)
        for _ in range(11):
            app_clock.now += HOUR
            assert _logged_in(_dashboard(client))
        app_clock.now += HOUR
        assert _kicked_out(_dashboard(client))

    def test_denylisted_mid_session(self, app, client, caplog):
        _login(client)
        other = app.test_client()
        _login(other)
        assert _logged_in(_dashboard(client))
        cfg = app.config["GUISE"]
        app.config["GUISE"] = dataclasses.replace(cfg, denied_users=cfg.denied_users | {"alice"})
        with caplog.at_level(logging.WARNING):
            assert _kicked_out(_dashboard(client))
        assert any("user=alice reason=denied" in m for m in _rejections(caplog))
        # Every session of that user is revoked, not just the one that hit.
        app.config["GUISE"] = cfg
        assert _kicked_out(_dashboard(other))

    def test_pre_upgrade_cookie_rejected(self, client, caplog):
        with client.session_transaction() as s:
            s["user"] = "alice"
        with caplog.at_level(logging.WARNING):
            assert _kicked_out(_dashboard(client))
        assert any("reason=no_session" in m for m in _rejections(caplog))
        with client.session_transaction() as s:
            assert "user" not in s

    def test_unknown_sid_rejected(self, client, caplog):
        with client.session_transaction() as s:
            s["user"] = "alice"
            s["sid"] = "forged"
        with caplog.at_level(logging.WARNING):
            assert _kicked_out(_dashboard(client))
        assert any("reason=unknown" in m for m in _rejections(caplog))

    def test_sid_of_other_user_rejected(self, client):
        _login(client, "bob")
        with client.session_transaction() as s:
            s["user"] = "alice"
        assert _kicked_out(_dashboard(client))

    def test_expired_logged(self, client, app_clock, caplog):
        _login(client)
        app_clock.now += SESSION_LIFETIME
        with caplog.at_level(logging.WARNING):
            assert _kicked_out(_dashboard(client))
        assert any("reason=expired" in m for m in _rejections(caplog))

    def test_rejection_keeps_next(self, client):
        with client.session_transaction() as s:
            s["user"] = "alice"
        resp = client.get("/")
        assert "next=/" in resp.headers["Location"] or "next=%2F" in resp.headers["Location"]

    def test_no_cookie_redirects_silently(self, client, caplog):
        with caplog.at_level(logging.WARNING):
            resp = client.get("/")
        assert _kicked_out(resp)
        assert not _rejections(caplog)

    def test_csrf_only_session_redirects_silently(self, client, caplog):
        client.get("/login")  # may set a csrf token in the session
        with caplog.at_level(logging.WARNING):
            assert _kicked_out(client.get("/"))
        assert not _rejections(caplog)

    def test_header_shows_user_only_for_valid_session(self, app, client):
        _login(client)
        assert b'class="user">alice' in _dashboard(client).data
        thief = app.test_client()
        _copy_cookie(client, thief)
        with client.session_transaction() as s:
            s["csrf_token"] = "t"
        client.post("/logout", data={"csrf_token": "t"})
        # The revoked copy no longer shows as logged in, even on a page
        # without login_required.
        assert b'class="user"' not in thief.get("/login").data

    def test_rejected_delete_does_nothing(self, client):
        with client.session_transaction() as s:
            s["user"] = "alice"
            s["sid"] = "forged"
            s["csrf_token"] = "t"
        with patch("app.aliases._run_setup") as mock_run:
            resp = client.post("/aliases/g-1@example.com/delete", data={"csrf_token": "t"})
        assert _kicked_out(resp)
        mock_run.assert_not_called()
