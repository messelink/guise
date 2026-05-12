import imaplib
import re
import ssl
from functools import wraps
from typing import Callable

from flask import Flask, current_app, flash, g, redirect, render_template, request, session, url_for

from .config import Config


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


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

    Hostname verification is disabled because we reach the mailserver by its
    Docker network name (typically `mailserver`), not by the cert's CN (the
    public mail hostname). Encryption is still in place; trust comes from the
    Docker bridge boundary.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
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
    except (OSError, ssl.SSLError):
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
                flash("This account is not permitted to use Guise.", "error")
            elif not password:
                flash("Password required.", "error")
            elif _imap_check(username, password, config):
                session.clear()
                session["user"] = username
                next_url = request.args.get("next") or url_for("main.index")
                if not next_url.startswith("/"):
                    next_url = url_for("main.index")
                return redirect(next_url)
            else:
                flash("Login failed.", "error")
        return render_template("login.html")

    @bp.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("auth.login"))

    app.register_blueprint(bp)
