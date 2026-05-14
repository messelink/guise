import logging
from datetime import timedelta

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import load_config
from .extensions import limiter
from . import api, auth, routes


__version__ = "0.3.0"


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
    limiter.init_app(app)
    auth.register(app)
    routes.register(app)
    api.register(app)
    return app
