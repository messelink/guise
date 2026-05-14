import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Flask app with required env vars and an isolated data dir."""
    monkeypatch.setenv("GUISE_DOMAIN", "example.com")
    monkeypatch.setenv("GUISE_DENIED_USERS", "gitea,immich")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()
