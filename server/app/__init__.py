from datetime import timedelta

from flask import Flask

from .config import load_config
from . import auth, routes


__version__ = "0.1.0"


def create_app() -> Flask:
    app = Flask(__name__)
    config = load_config()
    app.config.update(
        SECRET_KEY=config.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.session_cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        GUISE=config,
        GUISE_VERSION=__version__,
    )
    app.jinja_env.globals["csrf_token"] = auth.csrf_token
    app.before_request(auth.validate_csrf)
    auth.register(app)
    routes.register(app)
    return app
