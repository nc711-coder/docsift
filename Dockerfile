# DocSift: API + web page, for Google Cloud Run (or any Docker host).
#   docker build -t docsift .
#   docker run -p 8080:8080 --env-file .env docsift
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_THREAD_LIMIT=1

# Tesseract reads scans and photos; libglib is the one system library OpenCV (headless) needs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Only what the server needs (no tests or evals). The samples are the demo's one-click documents.
COPY docsift/ docsift/
COPY web/ web/
COPY samples/docs/ samples/docs/
COPY samples/index.csv samples/index.csv

# Don't run as root.
RUN useradd --create-home appuser
USER appuser

# Cloud Run sets $PORT. --proxy-headers: behind Google's load balancer, links in emails must say https.
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn docsift.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
