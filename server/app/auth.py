import imaplib
import re
import secrets
import ssl
from enum import Enum
from functools import wraps
from typing import Callable
from urllib.parse import urlparse

from flask import (
    Flask, abort, current_app, flash, g, make_response, redirect, render_template, request,
    session, url_for,
)

from .config import Config
from .extensions import limiter


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOGIN_ENDPOINT = "auth.login"


def csrf_token() -> str:
    """Return the per-session CSRF token, creating it on first access."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _csrf_valid(submitted: str | None, expected: str | None) -> bool:
    if not submitted or not expected:
        return False
    return secrets.compare_digest(submitted, expected)


def validate_csrf() -> None:
    """Before-request hook: 400 on unsafe methods with missing/wrong token.

    Skipped for /api/* — that surface uses header-based auth (no session
    cookie), so CSRF doesn't apply.
    """
    if request.method not in UNSAFE_METHODS:
        return
    if request.path.startswith("/api/"):
        return
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("csrf_token")
    if not _csrf_valid(submitted, expected):
        abort(400, description="Invalid CSRF token")


def _safe_next_url(raw: str | None, default: str) -> str:
    """Reject open-redirect attempts. Only same-site paths starting with a
    single '/' are permitted; '//host' and '/\\host' are rejected.
    """
    if not raw:
        return default
    if not raw.startswith("/"):
        return default
    if raw.startswith(("//", "/\\")):
        return default
    # Control characters (CR/LF, tab, DEL) make Werkzeug refuse to build the
    # Location header, which surfaced as a 500 after a successful login.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        return default
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return default
    return raw


def _strip_domain(username: str, domain: str) -> str | None:
    """Convenience: if the user typed a full email address with our configured
    domain, strip the @suffix and use the short form.

    Returns the short username on success (or the unchanged input if no `@`),
    or None if `@` is present with a different domain (so the caller can flash
    a clearer error than the username regex would produce).
    """
    if "@" not in username:
        return username
    local, _, domain_part = username.partition("@")
    if domain_part.lower() == domain.lower():
        return local
    return None


def _imap_check(username: str, password: str, config: Config) -> bool:
    """Authenticate against the mailserver's dovecot via IMAPS.

    Cert validation is on by default (CERT_REQUIRED against the system trust
    store, or `GUISE_IMAP_CAFILE` if set). Hostname verification is off because
    we typically reach the mailserver by its Docker network name (`mailserver`),
    not by the cert's CN (the public mail hostname).

    `GUISE_IMAP_INSECURE=1` disables all TLS verification — only use if you
    genuinely need it and understand the MITM exposure.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    if config.imap_insecure:
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.verify_mode = ssl.CERT_REQUIRED
        if config.imap_cafile:
            ctx.load_verify_locations(cafile=config.imap_cafile)
    full = f"{username}@{config.domain}"
    try:
        with imaplib.IMAP4_SSL(config.imap_host, config.imap_port, ssl_context=ctx, timeout=10) as imap:
            imap.login(full, password)
            try:
                imap.logout()
            except Exception:
                pass
        return True
    except imaplib.IMAP4.error:
        return False
    except OSError:
        # ssl.SSLError is a subclass of OSError, so it's covered here too.
        return False


class Outcome(Enum):
    OK = "ok"
    FAILED = "failed"
    DENIED = "denied"
    THROTTLED = "throttled"
    BUSY = "busy"


def check_credentials(username: str, password: str, ip: str, config: Config) -> Outcome:
    """Check a shape-valid short username + non-empty password.

    Shared by the web login and the API so both apply the same backoff,
    denylist and IMAP check. Order matters: the backoff runs first so a
    throttled attempt never reaches dovecot, and the denylist runs before
    IMAP so service-account passwords are never sent there. Denied logins
    count as failures, so the denylist can't be told apart by backoff.
    """
    backoff = current_app.extensions["guise_backoff"]
    claim = backoff.begin(username, ip)
    if claim != "ok":
        return Outcome.THROTTLED if claim == "throttled" else Outcome.BUSY
    outcome = Outcome.FAILED
    try:
        if username in config.denied_users:
            outcome = Outcome.DENIED
        elif _imap_check(username, password, config):
            outcome = Outcome.OK
    finally:
        # Always release the claim from begin(), even if the check raised;
        # an unexpected error counts as a failure.
        if outcome is Outcome.OK:
            backoff.record_success(username, ip)
        else:
            backoff.record_failure(username, ip)
    return outcome


def retry_after_seconds(username: str, ip: str) -> int:
    """Whole seconds to wait before retrying, rounded up; for Retry-After."""
    wait = current_app.extensions["guise_backoff"].retry_after(username, ip)
    return max(1, int(wait) + (wait % 1 > 0))


def _session_problem(user: object, sid: object, config: Config) -> str | None:
    """Why the current session isn't valid, or None if it is."""
    if not user or not sid:
        # A cookie from before server-side sessions has a user but no sid.
        return "no_session"
    registry = current_app.extensions["guise_sessions"]
    status = registry.status(sid, user)
    if status != "ok":
        return status
    if user in config.denied_users:
        return "denied"
    return None


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args, **kwargs):
        config: Config = current_app.config["GUISE"]
        user = session.get("user")
        sid = session.get("sid")
        if user is None and sid is None:
            # Not logged in at all: a normal visit, not worth a log line.
            return redirect(url_for(LOGIN_ENDPOINT, next=request.path))
        reason = _session_problem(user, sid, config)
        if reason is not None:
            registry = current_app.extensions["guise_sessions"]
            if reason == "denied":
                registry.revoke_user(user)
            elif sid is not None:
                registry.revoke(sid)
            session.clear()
            current_app.logger.warning(
                "SESSION_REJECTED user=%s reason=%s ip=%s", user, reason, request.remote_addr,
            )
            return redirect(url_for(LOGIN_ENDPOINT, next=request.path))
        g.user = user
        g.target_email = f"{user}@{config.domain}"
        return view(*args, **kwargs)
    return wrapped


def register(app: Flask) -> None:
    from flask import Blueprint
    bp = Blueprint("auth", __name__)

    @bp.route("/login", methods=["GET", "POST"])
    @limiter.limit("20 per minute")
    def login():
        config: Config = current_app.config["GUISE"]
        if request.method == "POST":
            raw = (request.form.get("username") or "").strip().lower()
            password = request.form.get("password") or ""
            username = _strip_domain(raw, config.domain)
            if username is None:
                flash(
                    f"This instance manages aliases for {config.domain}. "
                    "Enter your short username (without @domain).",
                    "error",
                )
            elif not USERNAME_RE.match(username):
                flash("Invalid username.", "error")
            # Before the denylist check: otherwise an empty password tells a
            # denied user ("Login failed.") apart from everyone else.
            elif not password:
                flash("Password required.", "error")
            else:
                ip = request.remote_addr
                outcome = check_credentials(username, password, ip, config)
                if outcome is Outcome.OK:
                    registry = current_app.extensions["guise_sessions"]
                    # Logging in again replaces the old session rather than
                    # leaving it valid alongside the new one.
                    registry.revoke(session.get("sid"))
                    # Clear before setting keys, so no pre-login state
                    # (session fixation) carries over.
                    session.clear()
                    session["user"] = username
                    session["sid"] = registry.create(username)
                    session.permanent = True
                    current_app.logger.info("LOGIN user=%s ip=%s", username, ip)
                    default_next = url_for("main.index")
                    next_url = _safe_next_url(request.args.get("next"), default_next)
                    return redirect(next_url)
                if outcome in (Outcome.THROTTLED, Outcome.BUSY):
                    busy = outcome is Outcome.BUSY
                    current_app.logger.warning(
                        "%s user=%s ip=%s", "LOGIN_BUSY" if busy else "LOGIN_THROTTLED", username, ip,
                    )
                    flash(
                        "Another login for this account is in progress. Try again in a moment."
                        if busy else "Too many failed attempts. Try again later.",
                        "error",
                    )
                    resp = make_response(render_template("login.html"), 429)
                    resp.headers["Retry-After"] = "1" if busy else str(retry_after_seconds(username, ip))
                    return resp
                if outcome is Outcome.DENIED:
                    current_app.logger.warning("LOGIN_DENIED user=%s ip=%s", username, ip)
                else:
                    current_app.logger.warning("LOGIN_FAILED user=%s ip=%s", username, ip)
                # Same message for denied and wrong password, so the response
                # doesn't reveal who is on the denylist. The log keeps the reason.
                flash("Login failed.", "error")
        return render_template("login.html")

    @bp.route("/logout", methods=["POST"])
    def logout():
        user = session.get("user")
        # Revoke server-side so a copy of this cookie stops working too.
        current_app.extensions["guise_sessions"].revoke(session.get("sid"))
        session.clear()
        if user:
            current_app.logger.info(
                "LOGOUT user=%s ip=%s", user, request.remote_addr,
            )
        return redirect(url_for(LOGIN_ENDPOINT))

    app.register_blueprint(bp)
