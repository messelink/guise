import logging
from datetime import timedelta

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import load_config
from .backoff import LoginBackoff
from .extensions import limiter
from . import api, auth, routes


__version__ = "0.4.0"

# All scripts and styles are served from /static, so the policy needs no
# 'unsafe-inline'. frame-ancestors 'none' (plus X-Frame-Options for older
# browsers) stops the dashboard being framed for clickjacking.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def _security_headers(response):
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # Pages show the user's alias list; keep them out of browser and proxy
    # caches. Static assets are not user-specific and stay cacheable.
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _bridge_logger_to_gunicorn(app: Flask) -> None:
    """Make app.logger emit through gunicorn's error log so audit INFO lines
    (LOGIN, LOGOUT, ALIAS_CREATED, ALIAS_DELETED) reach `docker logs`. Outside
    gunicorn (pytest, `flask run`) the gunicorn logger has no handlers and this
    is a no-op.
    """
    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)


def create_app() -> Flask:
    app = Flask(__name__)
    _bridge_logger_to_gunicorn(app)
    # Trust one hop of X-Forwarded-* headers from the reverse proxy so
    # `request.remote_addr` reflects the real client IP (for rate-limiting
    # and audit logging) and `request.is_secure` reflects HTTPS termination
    # at the proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    config = load_config()
    app.config.update(
        SECRET_KEY=config.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.session_cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=16 * 1024,
        GUISE=config,
        GUISE_VERSION=__version__,
    )
    app.jinja_env.globals["csrf_token"] = auth.csrf_token
    app.before_request(auth.validate_csrf)
    app.after_request(_security_headers)
    limiter.init_app(app)
    # Per-app instance (not a module global) so each app, and each test,
    # starts with clean counters.
    app.extensions["guise_backoff"] = LoginBackoff()
    auth.register(app)
    routes.register(app)
    api.register(app)
    return app
