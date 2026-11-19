FROM python:3.12-slim AS builder

WORKDIR /build

COPY services/relay/requirements.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AWS_EC2_METADATA_DISABLED=true

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

RUN mkdir -p /app/certs \
    && chmod 0755 /app /app/certs

ADD --chmod=0444 \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    /app/certs/global-bundle.pem

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app shared ./shared
COPY --chown=app:app services/relay/app ./app
COPY --chown=app:app scripts/__init__.py scripts/init_queue.py scripts/reconcile_inventory.py ./scripts/

RUN chmod -R a+rX /app

USER 10001:10001

CMD ["python", "-m", "app.main"]