"""Web-surface hardening: security headers, ?next= control characters,
denylist disclosure, and what the API failure log records."""
import logging
from unittest.mock import MagicMock, patch

from app import auth

DEFAULT = "/"


def _imap_ok(mock_imap):
    instance = MagicMock()
    instance.__enter__.return_value = instance
    mock_imap.return_value = instance
    return instance


def _login(client, username, next_url=None):
    with client.session_transaction() as s:
        s["csrf_token"] = "t"
    url = "/login" if next_url is None else "/login?next=" + next_url
    return client.post(
        url, data={"username": username, "password": "pw", "csrf_token": "t"},  # NOSONAR — test fixture, not a real credential
    )


class TestSecurityHeaders:
    def test_page_headers(self, client):
        resp = client.get("/login")
        csp = resp.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert resp.headers["Cache-Control"] == "no-store"

    def test_api_response_not_cached(self, client):
        resp = client.post("/api/alias/random/new")
        assert resp.status_code == 401
        assert resp.headers["Cache-Control"] == "no-store"

    def test_static_stays_cacheable(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "no-store" not in resp.headers.get("Cache-Control", "")

    def test_no_inline_script_or_style_in_templates(self, app):
        # The CSP has no 'unsafe-inline', so any of these would be blocked.
        import re
        from pathlib import Path
        inline = re.compile(r"<script(?![^>]*\bsrc=)|<style\b|\sstyle=|\son[a-z]+=", re.I)
        for tpl in (Path(app.root_path) / "templates").glob("*.html"):
            match = inline.search(tpl.read_text())
            assert match is None, f"inline script/style in {tpl.name}: {match.group(0)!r}"


class TestNextControlCharacters:
    def test_crlf_rejected(self):
        assert auth._safe_next_url("/foo\r\nSet-Cookie: x=1", DEFAULT) == DEFAULT

    def test_tab_rejected(self):
        assert auth._safe_next_url("/\t/evil.com", DEFAULT) == DEFAULT

    def test_del_rejected(self):
        assert auth._safe_next_url("/foo\x7f", DEFAULT) == DEFAULT

    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_crlf_next_after_login_redirects_home(self, mock_imap, client):
        # Used to raise in Werkzeug and return 500 after a successful login.
        _imap_ok(mock_imap)
        resp = _login(client, "alice", next_url="/x%0D%0AInjected:1")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"


class TestDenylistNotDisclosed:
    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_denied_user_sees_generic_failure(self, mock_imap, client, caplog):
        with caplog.at_level(logging.WARNING):
            resp = _login(client, "gitea")
        assert resp.status_code == 200
        assert b"Login failed." in resp.data
        assert b"not permitted" not in resp.data
        mock_imap.assert_not_called()
        assert any("LOGIN_DENIED user=gitea" in r.message for r in caplog.records)

    def test_empty_password_same_for_denied_and_allowed(self, client):
        def empty_pw(username):
            with client.session_transaction() as s:
                s["csrf_token"] = "t"
            return client.post(
                "/login", data={"username": username, "password": "", "csrf_token": "t"},
            ).data
        assert b"Password required." in empty_pw("gitea")
        assert b"Password required." in empty_pw("alice")


class TestApiFailureLog:
    def test_invalid_username_not_logged(self, client, caplog):
        # Looks like a password pasted into the username field.
        with caplog.at_level(logging.WARNING):
            client.post(
                "/api/alias/random/new",
                headers={"Authentication": "Hunter2!Secret:pw"},  # NOSONAR — test fixture, not a real credential
            )
        failed = [r.message for r in caplog.records if "LOGIN_FAILED" in r.message]
        assert failed
        assert all("Hunter2" not in m for m in failed)
        assert any("user=<invalid>" in m for m in failed)

    def test_valid_username_still_logged(self, client, caplog):
        with caplog.at_level(logging.WARNING):
            client.post("/api/alias/random/new", headers={"Authentication": "gitea:pw"})  # NOSONAR — test fixture, not a real credential
        assert any("LOGIN_DENIED user=gitea" in r.message for r in caplog.records)
