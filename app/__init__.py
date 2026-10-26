"""Membuat dan mengonfigurasi instance Flask utama aplikasi konverter."""

import os
from pathlib import Path
from pandas import DataFrame
from flask import Flask

from app.logging import configure_logging
from app.middleware.request_context import register_request_logging
from app.routes import main_bp, logs_bp


def create_app():
    """Siapkan konfigurasi upload/output, folder storage, logging, dan blueprint route."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

    max_upload_mb = int(os.environ.get("KONVERTER_MAX_UPLOAD_MB", "128"))
    app.config["MAX_UPLOAD_MB"] = max_upload_mb
    app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024

    root_path = Path(app.root_path).parent
    app.config["UPLOAD_FOLDER"] = root_path / "storage" / "uploads"
    app.config["OUTPUT_FOLDER"] = root_path / "storage" / "outputs"

    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)
    app.config["OUTPUT_FOLDER"].mkdir(parents=True, exist_ok=True)

    # Logging & request context middleware
    configure_logging(app)
    register_request_logging(app)

    # Blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(logs_bp)

    return app
