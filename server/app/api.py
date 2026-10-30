"""SimpleLogin-compatible HTTP API.

Currently implements the subset needed for Bitwarden's "Forwarded email
alias → SimpleLogin (self-hosted server)" username generator.
"""
from flask import Blueprint, Flask, current_app, jsonify, request

from . import aliases
from .auth import USERNAME_RE, Outcome, check_credentials, retry_after_seconds
from .config import Config
from .extensions import limiter


bp = Blueprint("api", __name__, url_prefix="/api")

MAX_ATTEMPTS = 20


def _parse_authentication_header(value: str) -> tuple[str, str] | None:
    """Split a SimpleLogin-style `Authentication: user:password` header."""
    if not value or ":" not in value:
        return None
    username, password = value.split(":", 1)
    return username.strip().lower(), password


def _authenticate(config: Config) -> tuple[str | None, Outcome, int]:
    """Return (username, outcome, retry_after).

    username is set only on Outcome.OK; retry_after (seconds) only on
    Outcome.THROTTLED.
    """
    creds = _parse_authentication_header(request.headers.get("Authentication", ""))
    if not creds:
        return None, Outcome.FAILED, 0
    username, password = creds
    ip = request.remote_addr
    if not USERNAME_RE.match(username):
        # A malformed value may be a password pasted into the wrong field.
        current_app.logger.warning("LOGIN_FAILED user=<invalid> ip=%s via=api", ip)
        return None, Outcome.FAILED, 0
    if not password:
        current_app.logger.warning("LOGIN_FAILED user=%s ip=%s via=api", username, ip)
        return None, Outcome.FAILED, 0
    outcome = check_credentials(username, password, ip, config)
    if outcome is Outcome.OK:
        return username, outcome, 0
    event = {
        Outcome.THROTTLED: "LOGIN_THROTTLED",
        Outcome.BUSY: "LOGIN_BUSY",
        Outcome.DENIED: "LOGIN_DENIED",
    }.get(outcome, "LOGIN_FAILED")
    current_app.logger.warning("%s user=%s ip=%s via=api", event, username, ip)
    retry = {Outcome.THROTTLED: retry_after_seconds(username, ip), Outcome.BUSY: 1}.get(outcome, 0)
    return None, outcome, retry


@bp.route("/alias/random/new", methods=["POST"])
@limiter.limit("30 per minute")
def create_random_alias():
    config: Config = current_app.config["GUISE"]
    username, outcome, retry = _authenticate(config)
    if outcome in (Outcome.THROTTLED, Outcome.BUSY):
        resp = jsonify({
            "error": "login in progress" if outcome is Outcome.BUSY else "too many failed attempts",
        })
        resp.status_code = 429
        resp.headers["Retry-After"] = str(retry)
        return resp
    if not username:
        return jsonify({"error": "authentication required"}), 401

    target = f"{username}@{config.domain}"

    hostname = (request.args.get("hostname") or "").strip()
    slug = aliases.hostname_to_label(hostname) if config.api_autolabel else ""

    try:
        existing = aliases.list_aliases(config.mailserver_container)
    except RuntimeError as exc:
        current_app.logger.warning("setup alias list failed via api: %s", exc)
        return jsonify({"error": "could not list existing aliases"}), 500

    existing_locals = {a.split("@", 1)[0] for a, _ in existing}

    local_part = None
    for _ in range(MAX_ATTEMPTS):
        candidate = aliases.make_local_part(config.tag, slug)
        if candidate not in existing_locals:
            local_part = candidate
            break
    if local_part is None:
        return jsonify({"error": "could not generate unique alias"}), 500

    alias_addr = f"{local_part}@{config.domain}"
    try:
        aliases.add_alias(config.mailserver_container, alias_addr, target)
    except (RuntimeError, ValueError) as exc:
        current_app.logger.warning(
            "ALIAS_CREATE_FAILED user=%s alias=%s ip=%s via=api err=%s",
            username, alias_addr, request.remote_addr, exc,
        )
        return jsonify({"error": "could not create alias"}), 500

    current_app.logger.info(
        "ALIAS_CREATED user=%s alias=%s ip=%s via=api slug=%s",
        username, alias_addr, request.remote_addr, slug,
    )
    return jsonify({"alias": alias_addr}), 201


def register(app: Flask) -> None:
    app.register_blueprint(bp)
