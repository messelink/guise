from flask import Blueprint, Flask, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import aliases
from .auth import login_required
from .config import Config


def register(app: Flask) -> None:
    bp = Blueprint("main", __name__)

    @bp.route("/")
    @login_required
    def index():
        config: Config = current_app.config["GUISE"]
        try:
            rows = aliases.list_aliases(config.mailserver_container)
        except RuntimeError as exc:
            flash(f"Could not read alias list: {exc}", "error")
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
    def create():
        config: Config = current_app.config["GUISE"]
        raw_label = request.form.get("label", "")
        if len(raw_label) > 80:
            flash("Label too long (max 80 characters).", "error")
            return redirect(url_for("main.index"))
        slug = aliases.slugify(raw_label)
        if raw_label and not slug:
            flash("Label has no usable characters after slugifying.", "error")
            return redirect(url_for("main.index"))

        try:
            existing = aliases.list_aliases(config.mailserver_container)
        except RuntimeError as exc:
            flash(f"Could not check existing aliases: {exc}", "error")
            return redirect(url_for("main.index"))
        existing_locals = {alias.split("@", 1)[0] for alias, _ in existing}

        for _ in range(20):
            local_part = aliases.make_local_part(config.tag, slug)
            if local_part not in existing_locals:
                break
        else:
            flash("Could not generate a unique alias after 20 tries.", "error")
            return redirect(url_for("main.index"))

        alias_addr = f"{local_part}@{config.domain}"
        try:
            aliases.add_alias(config.mailserver_container, alias_addr, g.target_email)
        except RuntimeError as exc:
            flash(f"Failed to create alias: {exc}", "error")
            return redirect(url_for("main.index"))
        flash(f"Created {alias_addr}", "success")
        return redirect(url_for("main.index"))

    @bp.route("/aliases/<path:local_part>/delete", methods=["POST"])
    @login_required
    def delete(local_part: str):
        config: Config = current_app.config["GUISE"]
        if "@" not in local_part:
            abort(400)
        try:
            rows = aliases.list_aliases(config.mailserver_container)
        except RuntimeError as exc:
            flash(f"Could not read alias list: {exc}", "error")
            return redirect(url_for("main.index"))
        match = next(((a, t) for a, t in rows if a == local_part), None)
        if not match:
            flash("Alias not found.", "error")
            return redirect(url_for("main.index"))
        alias_addr, target = match
        if target != g.target_email:
            abort(403)
        try:
            aliases.del_alias(config.mailserver_container, alias_addr, target)
        except RuntimeError as exc:
            flash(f"Failed to delete alias: {exc}", "error")
            return redirect(url_for("main.index"))
        flash(f"Deleted {alias_addr}", "success")
        return redirect(url_for("main.index"))

    app.register_blueprint(bp)
