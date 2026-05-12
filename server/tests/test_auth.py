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


class TestSafeNextUrl:
    DEFAULT = "/"

    def test_empty_returns_default(self):
        assert auth._safe_next_url("", self.DEFAULT) == self.DEFAULT
        assert auth._safe_next_url(None, self.DEFAULT) == self.DEFAULT

    def test_simple_path_preserved(self):
        assert auth._safe_next_url("/dashboard", self.DEFAULT) == "/dashboard"

    def test_path_with_query_preserved(self):
        assert auth._safe_next_url("/foo?x=1&y=2", self.DEFAULT) == "/foo?x=1&y=2"

    def test_root_preserved(self):
        assert auth._safe_next_url("/", self.DEFAULT) == "/"

    def test_protocol_relative_rejected(self):
        assert auth._safe_next_url("//evil.com/path", self.DEFAULT) == self.DEFAULT
        assert auth._safe_next_url("//evil.com", self.DEFAULT) == self.DEFAULT

    def test_backslash_smuggle_rejected(self):
        assert auth._safe_next_url("/\\evil.com", self.DEFAULT) == self.DEFAULT

    def test_absolute_url_rejected(self):
        assert auth._safe_next_url("https://evil.com/", self.DEFAULT) == self.DEFAULT
        assert auth._safe_next_url("http://evil.com/", self.DEFAULT) == self.DEFAULT

    def test_scheme_relative_rejected(self):
        # Some browsers / proxies treat these oddly
        assert auth._safe_next_url("javascript:alert(1)", self.DEFAULT) == self.DEFAULT

    def test_relative_path_rejected(self):
        # Must start with single '/' — anything else (including relative) defaults
        assert auth._safe_next_url("foo", self.DEFAULT) == self.DEFAULT


class TestCsrfValid:
    def test_match(self):
        assert auth._csrf_valid("abc123", "abc123") is True

    def test_mismatch(self):
        assert auth._csrf_valid("abc123", "xyz456") is False

    def test_empty_submitted(self):
        assert auth._csrf_valid("", "abc123") is False
        assert auth._csrf_valid(None, "abc123") is False

    def test_empty_expected(self):
        assert auth._csrf_valid("abc123", "") is False
        assert auth._csrf_valid("abc123", None) is False

    def test_both_empty(self):
        assert auth._csrf_valid("", "") is False
        assert auth._csrf_valid(None, None) is False


class TestStripDomain:
    def test_no_at_passes_through(self):
        assert auth._strip_domain("alice", "example.com") == "alice"

    def test_matching_domain_stripped(self):
        assert auth._strip_domain("alice@example.com", "example.com") == "alice"

    def test_domain_case_insensitive(self):
        assert auth._strip_domain("alice@EXAMPLE.COM", "example.com") == "alice"
        assert auth._strip_domain("alice@example.com", "Example.Com") == "alice"

    def test_mismatched_domain_returns_none(self):
        assert auth._strip_domain("alice@other.com", "example.com") is None

    def test_empty_local_with_matching_domain(self):
        # @example.com with empty local part — strips to "", caller's regex will reject
        assert auth._strip_domain("@example.com", "example.com") == ""

    def test_multiple_at_signs_use_first(self):
        # First @ is the separator; "example@example.com" as domain won't match
        assert auth._strip_domain("alice@example@example.com", "example.com") is None


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
