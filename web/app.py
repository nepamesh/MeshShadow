import hmac
import os

from flask import Flask, abort, request

from database.store import DataStore


def create_flask_app(store: DataStore) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["store"] = store

    proxy_secret = os.environ.get("PROXY_SECRET")

    @app.before_request
    def _require_proxy_secret():
        if not proxy_secret:
            return
        provided = request.headers.get("X-Proxy-Secret", "")
        if not hmac.compare_digest(provided, proxy_secret):
            abort(403)

    from .routes import bp
    app.register_blueprint(bp)

    return app
