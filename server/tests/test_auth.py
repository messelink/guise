from unittest.mock import MagicMock, patch

import pytest

from app import auth
from app.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(
        domain="example.com",
        tag="g-",
        denied_users=frozenset({"gitea", "immich"}),
        mailserver_container="mailserver",
        imap_host="mailserver",
        imap_port=993,
        data_dir=tmp_path,
        secret_key="test-secret",
        session_cookie_secure=False,
    )


class TestImapCheck:
    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_success(self, mock_imap, config):
        instance = MagicMock()
        instance.__enter__.return_value = instance
        mock_imap.return_value = instance
        assert auth._imap_check("alice", "password", config) is True
        instance.login.assert_called_once_with("alice@example.com", "password")

    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_bad_password(self, mock_imap, config):
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.login.side_effect = auth.imaplib.IMAP4.error("LOGIN failed")
        mock_imap.return_value = instance
        assert auth._imap_check("alice", "bad", config) is False

    @patch("app.auth.imaplib.IMAP4_SSL", side_effect=OSError("connection refused"))
    def test_network_error(self, mock_imap, config):
        assert auth._imap_check("alice", "p", config) is False

    @patch("app.auth.imaplib.IMAP4_SSL")
    def test_hostname_check_disabled(self, mock_imap, config):
        instance = MagicMock()
        instance.__enter__.return_value = instance
        mock_imap.return_value = instance
        auth._imap_check("alice", "p", config)
        _, kwargs = mock_imap.call_args
        ctx = kwargs["ssl_context"]
        assert ctx.check_hostname is False


class TestUsernameRegex:
    def test_accepts_simple(self):
        assert auth.USERNAME_RE.match("alice")

    def test_accepts_dot_username(self):
        assert auth.USERNAME_RE.match("first.last")

    def test_rejects_at(self):
        assert not auth.USERNAME_RE.match("user@example.com")

    def test_rejects_uppercase(self):
        assert not auth.USERNAME_RE.match("Alice")

    def test_rejects_empty(self):
        assert not auth.USERNAME_RE.match("")
