from flask import Flask, g

import db


def get_db():
    if "db_conn" not in g:
        g.db_conn = db.get_connection()
    return g.db_conn


def create_app():
    app = Flask(__name__)

    @app.teardown_appcontext
    def close_db(exception=None):
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    from app.routes import bp
    app.register_blueprint(bp)

    return app
