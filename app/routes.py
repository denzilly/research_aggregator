import secrets
import subprocess
import sys

import requests
from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for
from requests_oauthlib import OAuth1Session

import config
from app import get_db, queries
from ingest.keywords import parse_keywords

bp = Blueprint("main", __name__)

ZOTERO_REQUEST_TOKEN_URL = "https://www.zotero.org/oauth/request"
ZOTERO_AUTHORIZE_URL = "https://www.zotero.org/oauth/authorize"
ZOTERO_ACCESS_TOKEN_URL = "https://www.zotero.org/oauth/access"


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
    read_filter = request.args.get("read", "all")
    if read_filter not in ("all", "unread"):
        read_filter = "all"
    active_query_id = session.get("active_query_id")
    papers = queries.get_digest_papers(
        conn,
        window_days=queries.DIGEST_WINDOWS[window],
        query_id=active_query_id,
        unread_only=(read_filter == "unread"),
    )
    paper_ids = [p["id"] for p in papers]
    paper_folder_ids = queries.get_folder_ids_for_papers(paper_ids, conn)
    paper_query_ids = queries.get_query_ids_for_papers(paper_ids, conn)
    return render_template(
        "digest.html",
        papers=papers,
        paper_folder_ids=paper_folder_ids,
        paper_query_ids=paper_query_ids,
        window=window,
        read_filter=read_filter,
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
    paper_ids = [p["id"] for p in papers]
    paper_folder_ids = queries.get_folder_ids_for_papers(paper_ids, conn)
    paper_query_ids = queries.get_query_ids_for_papers(paper_ids, conn)
    return render_template(
        "search.html",
        papers=papers,
        q=q,
        source=source,
        since=since,
        until=until,
        folder=folder,
        paper_folder_ids=paper_folder_ids,
        paper_query_ids=paper_query_ids,
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
def delete_folder(folder_id):
    if not queries.delete_folder(folder_id, get_db()):
        abort(404)
    return "", 204


@bp.route("/papers/<path:paper_id>/folders/<int:folder_id>", methods=["PUT"])
def add_paper_to_folder(paper_id, folder_id):
    if not queries.add_paper_to_folder(paper_id, folder_id, get_db()):
        abort(404)
    return jsonify({"paper_id": paper_id, "folder_id": folder_id, "in_folder": True})


@bp.route("/papers/<path:paper_id>/folders/<int:folder_id>", methods=["DELETE"])
def remove_paper_from_folder(paper_id, folder_id):
    queries.remove_paper_from_folder(paper_id, folder_id, get_db())
    return jsonify({"paper_id": paper_id, "folder_id": folder_id, "in_folder": False})


@bp.route("/papers/<path:paper_id>/read", methods=["PUT"])
def mark_paper_read(paper_id):
    if not queries.mark_paper_read(paper_id, get_db()):
        abort(404)
    return jsonify({"paper_id": paper_id, "read": True})


@bp.route("/papers/<path:paper_id>/read", methods=["DELETE"])
def mark_paper_unread(paper_id):
    if not queries.mark_paper_unread(paper_id, get_db()):
        abort(404)
    return jsonify({"paper_id": paper_id, "read": False})


def _render_queries_page(conn, status=200, **extra):
    return render_template(
        "queries.html",
        query_list=queries.list_queries(conn),
        pipeline=_pipeline_status(conn),
        **extra,
    ), status


@bp.route("/queries", methods=["GET"])
def queries_page():
    return _render_queries_page(get_db())


@bp.route("/queries", methods=["POST"])
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
        zotero_oauth_configured=bool(config.ZOTERO_CLIENT_KEY and config.ZOTERO_CLIENT_SECRET),
        **_run_log_context(conn),
    )


@bp.route("/settings/run-now", methods=["POST"])
def run_now():
    conn = get_db()
    subprocess.Popen(
        [sys.executable, "-m", "ingest.ingest"],
        cwd=str(config.BASE_DIR),
    )
    return render_template(
        "settings.html",
        pipeline=_pipeline_status(conn),
        zotero_oauth_configured=bool(config.ZOTERO_CLIENT_KEY and config.ZOTERO_CLIENT_SECRET),
        **_run_log_context(conn),
        triggered=True,
    )


@bp.route("/zotero/login")
def zotero_login():
    """Step 1 of 3-legged OAuth 1.0a: fetch a temporary request token, stash
    its secret in this browser's (server-side, cookie-linked) session for
    /zotero/callback to pick back up, then send the user to zotero.org to
    approve access. Requires an app registered at zotero.org/oauth/apps —
    404s rather than erroring if that's not configured, same as any other
    route that doesn't exist, since the "Log in with Zotero" button is
    itself hidden in that case (see settings.html)."""
    if not (config.ZOTERO_CLIENT_KEY and config.ZOTERO_CLIENT_SECRET):
        abort(404)
    callback_uri = url_for("main.zotero_callback", _external=True)
    oauth = OAuth1Session(
        config.ZOTERO_CLIENT_KEY, client_secret=config.ZOTERO_CLIENT_SECRET, callback_uri=callback_uri
    )
    try:
        fetch_response = oauth.fetch_request_token(ZOTERO_REQUEST_TOKEN_URL)
    except Exception:
        abort(502)
    session["zotero_oauth_token"] = fetch_response.get("oauth_token")
    session["zotero_oauth_token_secret"] = fetch_response.get("oauth_token_secret")
    authorize_url = oauth.authorization_url(
        ZOTERO_AUTHORIZE_URL,
        library_access=1,
        notes_access=0,
        write_access=1,
        all_groups="none",
        name="phageDB",
    )
    return redirect(authorize_url)


@bp.route("/zotero/callback")
def zotero_callback():
    """Step 3: Zotero redirects back here with a verifier. Exchanged
    server-side (needs the app's client secret to sign the request), but
    the resulting key is handed to zotero_callback.html to store client-side
    in this browser's localStorage — same trust model as the manual
    API-key path, the server doesn't keep a copy."""
    if not (config.ZOTERO_CLIENT_KEY and config.ZOTERO_CLIENT_SECRET):
        abort(404)

    stored_token = session.pop("zotero_oauth_token", None)
    stored_secret = session.pop("zotero_oauth_token_secret", None)
    verifier = request.args.get("oauth_verifier")
    returned_token = request.args.get("oauth_token")

    if not (stored_token and stored_secret and verifier) or returned_token != stored_token:
        return render_template(
            "zotero_callback.html",
            error="That Zotero login didn't complete (or took too long) — try connecting again from Settings.",
        )

    oauth = OAuth1Session(
        config.ZOTERO_CLIENT_KEY,
        client_secret=config.ZOTERO_CLIENT_SECRET,
        resource_owner_key=stored_token,
        resource_owner_secret=stored_secret,
        verifier=verifier,
    )
    try:
        access_token = oauth.fetch_access_token(ZOTERO_ACCESS_TOKEN_URL)
    except Exception:
        return render_template(
            "zotero_callback.html", error="Couldn't complete the Zotero handshake — try again."
        )

    # Zotero's OAuth is simplified versus the spec: the returned
    # oauth_token_secret doubles as the permanent Zotero API key used for
    # subsequent Web API calls — there's no separate "exchange for an API
    # key" step. See https://www.zotero.org/support/dev/web_api/v3/oauth.
    key = access_token.get("oauth_token_secret")
    user_id = access_token.get("userID")
    if not (key and user_id):
        return render_template(
            "zotero_callback.html", error="Zotero didn't return a usable key — try again."
        )

    username = ""
    try:
        verify_resp = requests.get(f"https://api.zotero.org/keys/{key}", timeout=10)
        verify_resp.raise_for_status()
        username = verify_resp.json().get("username", "")
    except Exception:
        pass  # Non-fatal — the key still works without a display name.

    return render_template("zotero_callback.html", key=key, user_id=user_id, username=username)
