FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/
COPY app /app/app

RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
