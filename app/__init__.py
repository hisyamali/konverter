import os
from pathlib import Path
from pandas import DataFrame
from flask import Flask

from app.routes import main_bp


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "change-this-secret-key"

    max_upload_mb = int(os.environ.get("KONVERTER_MAX_UPLOAD_MB", "128"))
    app.config["MAX_UPLOAD_MB"] = max_upload_mb
    app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024

    root_path = Path(app.root_path).parent
    app.config["UPLOAD_FOLDER"] = root_path / "storage" / "uploads"
    app.config["OUTPUT_FOLDER"] = root_path / "storage" / "outputs"

    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)
    app.config["OUTPUT_FOLDER"].mkdir(parents=True, exist_ok=True)

    app.register_blueprint(main_bp)
    return app
