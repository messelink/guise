from flask import Flask

from .config import load_config
from . import auth, routes


def create_app() -> Flask:
    app = Flask(__name__)
    config = load_config()
    app.config.update(
        SECRET_KEY=config.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.session_cookie_secure,
        GUISE=config,
    )
    auth.register(app)
    routes.register(app)
    return app
