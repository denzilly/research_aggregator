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
    """Aggregates the most recent ingestion invocation's runs rows (one per
    query now, see ingest/ingest.py) into a single status for the header
    pill. A batch of one row (legacy pre-migration data, or a single-query
    invocation) behaves exactly like the old single-row status did."""
    batch = queries.get_latest_run_batch(conn)
    if not batch:
        return {"status": "never_run"}
    if any(r["status"] == "running" for r in batch):
        return {"status": "running"}
    errors = [r for r in batch if r["status"] == "error"]
    finished_at = max((r["finished_at"] for r in batch if r["finished_at"]), default=None)
    if errors:
        return {
            "status": "error",
            "finished_at": finished_at,
            "error_message": f"{len(errors)} of {len(batch)} quer{'y' if len(batch) == 1 else 'ies'} failed",
        }
    return {
        "status": "ok",
        "finished_at": finished_at,
        "new_papers_count": sum(r["new_papers_count"] or 0 for r in batch),
    }


def _safe_referrer():
    """Only trust request.referrer as a redirect target if it points back at
    this same host — a bare `redirect(request.referrer)` would otherwise be
    a minor open-redirect surface."""
    ref = request.referrer
    if ref and ref.startswith(request.host_url):
        return ref
    return None


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
    active_query_id = session.get("active_query_id")
    papers = queries.get_digest_papers(
        conn, window_days=queries.DIGEST_WINDOWS[window], query_id=active_query_id
    )
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
    active_query_id = session.get("active_query_id")

    papers = queries.search_papers(
        conn=conn,
        query=q,
        source=source,
        since=since,
        until=until,
        folder_id=folder_id,
        query_id=active_query_id,
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


def _validated_color(payload):
    """Returns the requested color if it's in the fixed palette, None if the
    field was omitted, or raises 400 — folder.color only ever renders as a raw
    CSS value, so it's validated against the known swatches server-side too,
    not just constrained by the <select>-like UI on the client."""
    if "color" not in payload:
        return None
    color = payload.get("color")
    if color is None:
        return None
    if color not in queries.FOLDER_COLORS:
        abort(400)
    return color


@bp.route("/folders", methods=["POST"])
@require_write_secret
def create_folder():
    conn = get_db()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or request.form.get("name") or "").strip()
    if not name:
        abort(400)
    color = _validated_color(payload)
    folder = queries.create_folder(name, color, conn)
    if folder is None:
        return jsonify({"error": "A folder with that name already exists."}), 409
    return jsonify(dict(folder)), 201


@bp.route("/folders/<int:folder_id>", methods=["PATCH"])
@require_write_secret
def rename_folder(folder_id):
    conn = get_db()
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            abort(400)
    color = _validated_color(payload)
    if name is None and color is None:
        abort(400)
    result = queries.update_folder(folder_id, name=name, color=color, conn=conn)
    if result is None:
        return jsonify({"error": "A folder with that name already exists."}), 409
    if result is False:
        abort(404)
    return jsonify({"id": folder_id, "name": name, "color": color})


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


def _render_queries_page(conn, status=200, **extra):
    return render_template(
        "queries.html",
        query_list=queries.list_queries(conn),
        write_secret_configured=bool(config.WRITE_SECRET),
        pipeline=_pipeline_status(conn),
        **extra,
    ), status


@bp.route("/queries", methods=["GET"])
def queries_page():
    return _render_queries_page(get_db())


@bp.route("/queries", methods=["POST"])
@require_write_secret
def create_query():
    conn = get_db()
    name = request.form.get("name", "").strip()
    keywords_raw = request.form.get("keywords", "")
    if not name or not parse_keywords(keywords_raw):
        abort(400)
    query = queries.create_query(name, keywords_raw, conn)
    if query == "max_reached":
        return _render_queries_page(
            conn, status=409,
            error=f"Maximum of {queries.MAX_QUERIES} queries reached — delete one to add another.",
        )
    if query is None:
        return _render_queries_page(conn, status=409, error="A query with that name already exists.")
    backfilled = queries.backfill_query_matches(query["id"], parse_keywords(keywords_raw), conn)
    return _render_queries_page(conn, created=query["name"], backfilled=backfilled)


@bp.route("/queries/<int:query_id>", methods=["POST"])
@require_write_secret
def update_query(query_id):
    conn = get_db()
    name = request.form.get("name", "").strip()
    keywords_raw = request.form.get("keywords", "")
    if not name or not parse_keywords(keywords_raw):
        abort(400)
    result = queries.update_query(query_id, name, keywords_raw, conn)
    if result is False:
        abort(404)
    if result is None:
        return _render_queries_page(conn, status=409, error="A query with that name already exists.")
    return _render_queries_page(conn, saved=query_id)


@bp.route("/queries/<int:query_id>/delete", methods=["POST"])
@require_write_secret
def delete_query(query_id):
    queries.delete_query(query_id, get_db())
    if session.get("active_query_id") == query_id:
        session.pop("active_query_id", None)
    return redirect(url_for("main.queries_page"))


@bp.route("/queries/<int:query_id>/activate", methods=["GET"])
def activate_query(query_id):
    if not queries.get_query(query_id, get_db()):
        abort(404)
    session["active_query_id"] = query_id
    return redirect(_safe_referrer() or url_for("main.digest"))


@bp.route("/queries/deactivate", methods=["GET"])
def deactivate_query():
    session.pop("active_query_id", None)
    return redirect(_safe_referrer() or url_for("main.digest"))


def _run_log_context(conn):
    runs = queries.list_recent_runs(conn=conn)
    known_costs = [r["cost_usd"] for r in runs if r["cost_usd"] is not None]
    return {
        "runs": runs,
        "runs_total_cost": sum(known_costs) if known_costs else None,
    }


@bp.route("/settings", methods=["GET"])
def settings():
    conn = get_db()
    return render_template(
        "settings.html",
        pipeline=_pipeline_status(conn),
        write_secret_configured=bool(config.WRITE_SECRET),
        **_run_log_context(conn),
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
        pipeline=_pipeline_status(conn),
        write_secret_configured=bool(config.WRITE_SECRET),
        **_run_log_context(conn),
        triggered=True,
    )
