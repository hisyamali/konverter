"""Entrypoint WSGI untuk server produksi seperti gunicorn."""

from app import create_app


app = create_app()
