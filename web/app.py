import hmac
import logging
import os

from flask import Flask, abort, request

from database.store import DataStore

log = logging.getLogger(__name__)


def create_flask_app(store: DataStore) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["store"] = store

    proxy_secret = os.environ.get("PROXY_SECRET") or None
    if not proxy_secret:
        log.warning("PROXY_SECRET is not set — web dashboard is unauthenticated")

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
