FROM python:3.12-slim AS builder

WORKDIR /build

COPY services/booking/requirements.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /app/keys

RUN mkdir -p /app/certs \
    && chmod 0755 /app /app/certs

ADD --chmod=0444 \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    /app/certs/global-bundle.pem

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app shared ./shared
COPY --chown=app:app services/booking/app ./app
COPY --chown=app:app scripts/local_token.py ./scripts/local_token.py

RUN chmod -R a+rX /app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "25"]