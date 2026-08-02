import subprocess
import sys
from functools import wraps

from flask import Blueprint, abort, jsonify, render_template, request

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


@bp.route("/")
def digest():
    conn = get_db()
    papers = queries.get_digest_papers(conn)
    return render_template("digest.html", papers=papers, pipeline=_pipeline_status(conn))


@bp.route("/search")
def search():
    conn = get_db()
    q = request.args.get("q", "")
    source = request.args.get("source", "")
    since = request.args.get("since", "")
    until = request.args.get("until", "")
    favorites_only = request.args.get("favorites") == "1"

    papers = queries.search_papers(
        conn=conn, query=q, source=source, since=since, until=until, favorites_only=favorites_only
    )
    return render_template(
        "search.html",
        papers=papers,
        q=q,
        source=source,
        since=since,
        until=until,
        favorites_only=favorites_only,
        pipeline=_pipeline_status(conn),
    )


@bp.route("/papers/<path:paper_id>/favorite", methods=["POST"])
@require_write_secret
def favorite(paper_id):
    conn = get_db()
    new_value = queries.toggle_favorite(paper_id, conn)
    if new_value is None:
        abort(404)
    return jsonify({"is_favorite": new_value})


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
