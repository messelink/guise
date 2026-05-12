import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    domain: str
    tag: str
    denied_users: frozenset[str]
    mailserver_container: str
    imap_host: str
    imap_port: int
    data_dir: Path
    secret_key: str
    session_cookie_secure: bool


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_or_create_secret_key(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    key_file = data_dir / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_urlsafe(64)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


def load_config() -> Config:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    denied = {
        u.strip().lower()
        for u in os.environ.get("GUISE_DENIED_USERS", "").split(",")
        if u.strip()
    }
    domain = os.environ.get("GUISE_DOMAIN", "").strip()
    if not domain:
        raise RuntimeError("GUISE_DOMAIN must be set")
    return Config(
        domain=domain,
        tag=os.environ.get("GUISE_TAG", "g-"),
        denied_users=frozenset(denied),
        mailserver_container=os.environ.get("GUISE_MAILSERVER_CONTAINER", "mailserver"),
        imap_host=os.environ.get("GUISE_IMAP_HOST", "mailserver"),
        imap_port=int(os.environ.get("GUISE_IMAP_PORT", "993")),
        data_dir=data_dir,
        secret_key=_load_or_create_secret_key(data_dir),
        session_cookie_secure=_bool("SESSION_COOKIE_SECURE", True),
    )
