from datetime import timedelta

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import load_config
from .extensions import limiter
from . import auth, routes


__version__ = "0.2.0"


def create_app() -> Flask:
    app = Flask(__name__)
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
    return app
