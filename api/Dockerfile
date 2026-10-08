FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY requirements.lock /app/
COPY app /app/app
COPY alembic.ini /app/
COPY alembic /app/alembic
COPY docker /app/docker

RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/snapshots \
    && chown -R appuser:appuser /app/data

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
