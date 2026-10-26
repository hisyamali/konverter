"""Routes package."""

from app.routes.main import main_bp
from app.routes.logs import logs_bp

__all__ = ["main_bp", "logs_bp"]