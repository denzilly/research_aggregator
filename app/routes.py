import secrets
import subprocess
import sys
from functools import wraps

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for

import config
from app import get_db, queries
from ingest.keywords import parse_keywords

bp = Blueprint("main", __name__)


def require_write_secret(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if config.WRITE_SECRET:
            provided = request.headers.get("X-Write-Secret") or request.form.get("secret") or request.args.get("secret")
            if provided != config.WRITE_SECRET:
                abort(403)
        return f(*args, **kwargs)
    return wrapper


def _pipeline_status(conn):
    run = queries.get_latest_run(conn)
    if not run:
        return {"status": "never_run"}
    return {
        "status": run["status"],
        "finished_at": run["finished_at"],
        "new_papers_count": run["new_papers_count"],
        "error_message": run["error_message"],
    }


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        provided = request.form.get("password", "")
        if secrets.compare_digest(provided, config.SITE_PASSWORD):
            session.clear()
            session["authenticated"] = True
            session.permanent = True
            return redirect(request.form.get("next") or url_for("main.digest"))
        error = "Wrong password."
    return render_template("login.html", error=error, next=request.args.get("next", ""))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/")
def digest():
    conn = get_db()
    window = request.args.get("window", queries.DEFAULT_DIGEST_WINDOW)
    if window not in queries.DIGEST_WINDOWS:
        window = queries.DEFAULT_DIGEST_WINDOW
    papers = queries.get_digest_papers(conn, window_days=queries.DIGEST_WINDOWS[window])
    paper_folder_ids = queries.get_folder_ids_for_papers([p["id"] for p in papers], conn)
    return render_template(
        "digest.html",
        papers=papers,
        paper_folder_ids=paper_folder_ids,
        window=window,
        pipeline=_pipeline_status(conn),
    )


@bp.route("/search")
def search():
    conn = get_db()
    q = request.args.get("q", "")
    source = request.args.get("source", "")
    since = request.args.get("since", "")
    until = request.args.get("until", "")
    folder_id = request.args.get("folder", type=int)
    folder = queries.get_folder(folder_id, conn) if folder_id else None

    papers = queries.search_papers(
        conn=conn, query=q, source=source, since=since, until=until, folder_id=folder_id
    )
    paper_folder_ids = queries.get_folder_ids_for_papers([p["id"] for p in papers], conn)
    return render_template(
        "search.html",
        papers=papers,
        q=q,
        source=source,
        since=since,
        until=until,
        folder=folder,
        paper_folder_ids=paper_folder_ids,
        pipeline=_pipeline_status(conn),
    )


@bp.route("/folders", methods=["POST"])
@require_write_secret
def create_folder():
    conn = get_db()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or request.form.get("name") or "").strip()
    if not name:
        abort(400)
    folder = queries.create_folder(name, conn)
    if folder is None:
        return jsonify({"error": "A folder with that name already exists."}), 409
    return jsonify(dict(folder)), 201


@bp.route("/folders/<int:folder_id>", methods=["PATCH"])
@require_write_secret
def rename_folder(folder_id):
    conn = get_db()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or request.form.get("name") or "").strip()
    if not name:
        abort(400)
    result = queries.rename_folder(folder_id, name, conn)
    if result is None:
        return jsonify({"error": "A folder with that name already exists."}), 409
    if result is False:
        abort(404)
    return jsonify({"id": folder_id, "name": name})


@bp.route("/folders/<int:folder_id>", methods=["DELETE"])
@require_write_secret
def delete_folder(folder_id):
    if not queries.delete_folder(folder_id, get_db()):
        abort(404)
    return "", 204


@bp.route("/papers/<path:paper_id>/folders/<int:folder_id>", methods=["PUT"])
@require_write_secret
def add_paper_to_folder(paper_id, folder_id):
    if not queries.add_paper_to_folder(paper_id, folder_id, get_db()):
        abort(404)
    return jsonify({"paper_id": paper_id, "folder_id": folder_id, "in_folder": True})


@bp.route("/papers/<path:paper_id>/folders/<int:folder_id>", methods=["DELETE"])
@require_write_secret
def remove_paper_from_folder(paper_id, folder_id):
    queries.remove_paper_from_folder(paper_id, folder_id, get_db())
    return jsonify({"paper_id": paper_id, "folder_id": folder_id, "in_folder": False})


@bp.route("/settings", methods=["GET"])
def settings():
    conn = get_db()
    return render_template(
        "settings.html",
        keywords_raw=queries.get_keywords_raw(conn),
        pipeline=_pipeline_status(conn),
        write_secret_configured=bool(config.WRITE_SECRET),
    )


@bp.route("/settings/keywords", methods=["POST"])
@require_write_secret
def save_keywords():
    conn = get_db()
    raw = request.form.get("keywords", "")
    if not parse_keywords(raw):
        abort(400)
    queries.save_keywords_raw(raw, conn)
    return render_template(
        "settings.html",
        keywords_raw=raw,
        pipeline=_pipeline_status(conn),
        write_secret_configured=bool(config.WRITE_SECRET),
        saved=True,
    )


@bp.route("/settings/run-now", methods=["POST"])
@require_write_secret
def run_now():
    conn = get_db()
    subprocess.Popen(
        [sys.executable, "-m", "ingest.ingest"],
        cwd=str(config.BASE_DIR),
    )
    return render_template(
        "settings.html",
        keywords_raw=queries.get_keywords_raw(conn),
        pipeline=_pipeline_status(conn),
        write_secret_configured=bool(config.WRITE_SECRET),
        triggered=True,
    )
