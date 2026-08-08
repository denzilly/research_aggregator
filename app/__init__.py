import os
from datetime import timedelta

from flask import Flask, g, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import db


def get_db():
    if "db_conn" not in g:
        g.db_conn = db.get_connection()
    return g.db_conn


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
    # Trust the one reverse-proxy hop (Cloudflare Tunnel) for scheme/host —
    # without this, url_for(..., _external=True) (used to build the Zotero
    # OAuth callback URL) would resolve to gunicorn's plain-http view of
    # itself instead of the site's real public https:// URL.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    @app.teardown_appcontext
    def close_db(exception=None):
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def inject_static_url():
        # Cache-busts static assets against the CDN/browser (research.btblog.dev
        # sits behind Cloudflare, which was independently caching style.css for
        # up to 4 hours past a deploy — this appends the file's on-disk mtime
        # as a query string so its URL actually changes when the file does).
        # Docker's `COPY . .` resets mtimes to build time regardless of
        # whether a given file changed, so every rebuilt image bumps every
        # asset's version — a little more aggressive than strictly necessary,
        # but simple and correct, and cheap at this app's asset count.
        def static_url(filename):
            path = os.path.join(app.static_folder, filename)
            try:
                version = int(os.path.getmtime(path))
            except OSError:
                version = 0
            return f"{url_for('static', filename=filename)}?v={version}"

        return {"static_url": static_url}

    @app.context_processor
    def inject_auth_flag():
        return {"site_password_configured": bool(config.SITE_PASSWORD)}

    @app.context_processor
    def inject_folders():
        from app import queries
        return {"folders": queries.list_folders(get_db()), "folder_colors": queries.FOLDER_COLORS}

    @app.context_processor
    def inject_queries():
        from app import queries
        conn = get_db()
        active_id = session.get("active_query_id")
        active_query = queries.get_query(active_id, conn) if active_id else None
        return {"queries": queries.list_queries(conn), "active_query": active_query}

    @app.before_request
    def require_login():
        if not config.SITE_PASSWORD:
            return
        if request.endpoint in ("main.login", "static"):
            return
        if not session.get("authenticated"):
            return redirect(url_for("main.login", next=request.path))

    from app.routes import bp
    app.register_blueprint(bp)

    return app
