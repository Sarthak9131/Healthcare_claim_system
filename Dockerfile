FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m pip install --no-cache-dir .

USER app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "claims_processing_cx.api:app", "--host", "0.0.0.0", "--port", "8000"]
