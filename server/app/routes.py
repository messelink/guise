from flask import Blueprint, Flask, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import aliases
from .auth import login_required
from .config import Config
from .extensions import limiter


INDEX_ENDPOINT = "main.index"

bp = Blueprint("main", __name__)


@bp.route("/", methods=["GET"])
@login_required
def index():
    config: Config = current_app.config["GUISE"]
    try:
        rows = aliases.list_aliases(config.mailserver_container)
    except RuntimeError as exc:
        current_app.logger.warning("setup alias list failed: %s", exc)
        flash("Could not read aliases. Please retry.", "error")
        rows = []
    view = aliases.build_view(rows, g.target_email, config.tag)
    return render_template(
        "index.html",
        user=g.user,
        target_email=g.target_email,
        domain=config.domain,
        tag=config.tag,
        managed=view["managed"],
        unmanaged=view["unmanaged"],
    )


@bp.route("/aliases", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def create():
    config: Config = current_app.config["GUISE"]
    raw_label = request.form.get("label", "")
    if len(raw_label) > 80:
        flash("Label too long (max 80 characters).", "error")
        return redirect(url_for(INDEX_ENDPOINT))
    slug = aliases.slugify(raw_label)
    if raw_label and not slug:
        flash("Label has no usable characters after slugifying.", "error")
        return redirect(url_for(INDEX_ENDPOINT))

    try:
        existing = aliases.list_aliases(config.mailserver_container)
    except RuntimeError as exc:
        current_app.logger.warning("setup alias list failed during create: %s", exc)
        flash("Could not check existing aliases. Please retry.", "error")
        return redirect(url_for(INDEX_ENDPOINT))
    existing_locals = {alias.split("@", 1)[0] for alias, _ in existing}

    for _ in range(20):
        local_part = aliases.make_local_part(config.tag, slug)
        if local_part not in existing_locals:
            break
    else:
        flash("Could not generate a unique alias after 20 tries.", "error")
        return redirect(url_for(INDEX_ENDPOINT))

    alias_addr = f"{local_part}@{config.domain}"
    try:
        aliases.add_alias(config.mailserver_container, alias_addr, g.target_email)
    except (RuntimeError, ValueError) as exc:
        current_app.logger.warning(
            "ALIAS_CREATE_FAILED user=%s alias=%s ip=%s err=%s",
            g.user, alias_addr, request.remote_addr, exc,
        )
        flash("Failed to create alias. Please retry.", "error")
        return redirect(url_for(INDEX_ENDPOINT))
    current_app.logger.info(
        "ALIAS_CREATED user=%s alias=%s ip=%s",
        g.user, alias_addr, request.remote_addr,
    )
    flash(f"Created {alias_addr}", "success")
    return redirect(url_for(INDEX_ENDPOINT))


@bp.route("/aliases/<path:local_part>/delete", methods=["POST"])
@login_required
def delete(local_part: str):
    config: Config = current_app.config["GUISE"]
    if "@" not in local_part:
        abort(400)
    try:
        rows = aliases.list_aliases(config.mailserver_container)
    except RuntimeError as exc:
        current_app.logger.warning("setup alias list failed during delete: %s", exc)
        flash("Could not read aliases. Please retry.", "error")
        return redirect(url_for(INDEX_ENDPOINT))
    match = next(((a, t) for a, t in rows if a == local_part), None)
    if not match:
        flash("Alias not found.", "error")
        return redirect(url_for(INDEX_ENDPOINT))
    alias_addr, target = match
    if target != g.target_email:
        current_app.logger.warning(
            "ALIAS_DELETE_FORBIDDEN user=%s alias=%s target=%s ip=%s",
            g.user, alias_addr, target, request.remote_addr,
        )
        abort(403)
    try:
        aliases.del_alias(config.mailserver_container, alias_addr, target)
    except (RuntimeError, ValueError) as exc:
        current_app.logger.warning(
            "ALIAS_DELETE_FAILED user=%s alias=%s ip=%s err=%s",
            g.user, alias_addr, request.remote_addr, exc,
        )
        flash("Failed to delete alias. Please retry.", "error")
        return redirect(url_for(INDEX_ENDPOINT))
    current_app.logger.info(
        "ALIAS_DELETED user=%s alias=%s ip=%s",
        g.user, alias_addr, request.remote_addr,
    )
    flash(f"Deleted {alias_addr}", "success")
    return redirect(url_for(INDEX_ENDPOINT))


def register(app: Flask) -> None:
    app.register_blueprint(bp)
