# Tahap 1: bangun Tailwind CSS dari template/JS/streamlit_app.py.
FROM node:22-alpine AS assets

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build:css


# Tahap 2: image aplikasi.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pakai CSS hasil build tahap aset agar selalu sinkron dengan source.
COPY --from=assets /build/flask_app/static/css/tailwind.css flask_app/static/css/tailwind.css
COPY --from=assets /build/flask_app/static/css/tailwind-streamlit.css flask_app/static/css/tailwind-streamlit.css

RUN mkdir -p storage/uploads storage/outputs

EXPOSE 7860

CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --worker-class gthread --timeout 0"]
