FROM python:3.12-slim AS builder

COPY tools/mysql-bootstrap/requirements.txt /tmp/requirements.txt

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install \
       --no-cache-dir \
       --require-hashes \
       -r /tmp/requirements.txt

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 app \
    && useradd \
       --uid 10001 \
       --gid 10001 \
       --create-home \
       --shell /usr/sbin/nologin \
       app \
    && mkdir -p /app/migrations /app/certs

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

COPY --chmod=0444 tools/mysql-bootstrap/bootstrap.py /app/bootstrap.py
COPY --chmod=0444 database/[0-9][0-9][0-9]_*.sql /app/migrations/

ADD --chmod=0444 \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    /app/certs/global-bundle.pem

RUN chmod 0755 /app /app/migrations /app/certs \
    && chmod 0444 /app/bootstrap.py /app/certs/global-bundle.pem

USER 10001:10001

RUN ["python", "-c", "import ssl; import bootstrap; bootstrap.load_migrations(bootstrap.MIGRATION_DIRECTORY); ssl.create_default_context(cafile=bootstrap.DEFAULT_CA); print('Non-root asset validation passed.')"]

ENTRYPOINT ["python", "/app/bootstrap.py"]