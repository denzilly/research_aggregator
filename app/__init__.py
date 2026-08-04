from datetime import timedelta

from flask import Flask, g, redirect, request, session, url_for

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

    @app.teardown_appcontext
    def close_db(exception=None):
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def inject_auth_flag():
        return {"site_password_configured": bool(config.SITE_PASSWORD)}

    @app.context_processor
    def inject_folders():
        from app import queries
        return {"folders": queries.list_folders(get_db())}

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
