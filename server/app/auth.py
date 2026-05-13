import imaplib
import re
import secrets
import ssl
from functools import wraps
from typing import Callable
from urllib.parse import urlparse

from flask import Flask, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .config import Config
from .extensions import limiter


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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
    """Before-request hook: 400 on unsafe methods with missing/wrong token."""
    if request.method not in UNSAFE_METHODS:
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


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("auth.login", next=request.path))
        g.user = session["user"]
        g.target_email = f"{g.user}@{current_app.config['GUISE'].domain}"
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
            elif username in config.denied_users:
                current_app.logger.warning(
                    "LOGIN_DENIED user=%s ip=%s", username, request.remote_addr,
                )
                flash("This account is not permitted to use guise.", "error")
            elif not password:
                flash("Password required.", "error")
            elif _imap_check(username, password, config):
                session.clear()
                session["user"] = username
                session.permanent = True
                current_app.logger.info(
                    "LOGIN user=%s ip=%s", username, request.remote_addr,
                )
                default_next = url_for("main.index")
                next_url = _safe_next_url(request.args.get("next"), default_next)
                return redirect(next_url)
            else:
                current_app.logger.warning(
                    "LOGIN_FAILED user=%s ip=%s", username, request.remote_addr,
                )
                flash("Login failed.", "error")
        return render_template("login.html")

    @bp.route("/logout", methods=["POST"])
    def logout():
        user = session.get("user")
        session.clear()
        if user:
            current_app.logger.info(
                "LOGOUT user=%s ip=%s", user, request.remote_addr,
            )
        return redirect(url_for("auth.login"))

    app.register_blueprint(bp)
