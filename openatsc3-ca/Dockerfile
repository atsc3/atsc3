# OpenATSC3 CA operator app (Django + PostgreSQL).
#
# Build context is the monorepo root (openatsc3-ca depends on the unpublished
# openatsc3-pki, so both are installed from the checkout):
#
#   docker build -f openatsc3-ca/Dockerfile -t openatsc3-ca .
#   docker run -e OPENATSC3_CA_DATABASE_URL=... -p 8000:8000 openatsc3-ca
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY openatsc3-pki/ ./openatsc3-pki/
COPY openatsc3-ca/ ./openatsc3-ca/

RUN pip install './openatsc3-pki' './openatsc3-ca[kms,stripe,aws,prod]'

# The materialized PEM tree and delivery scratch live outside the (read-only)
# app tree so they can be a volume/EFS mount; in production the online private
# keys are KMS, so the tree is public material rebuilt by `manage.py export`.
ENV OPENATSC3_CA_BASE_DIR=/data/ca-tree \
    OPENATSC3_CA_DELIVERY_DIR=/data/deliveries \
    OPENATSC3_CA_STATIC_ROOT=/srv/openatsc3-ca/staticfiles

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown -R app:app /data

WORKDIR /srv/openatsc3-ca

# collectstatic imports settings, which would otherwise start the embedded dev
# PostgreSQL; a throwaway URL avoids that (it is never used at runtime).
RUN OPENATSC3_CA_DATABASE_URL='postgresql://u:p@127.0.0.1:5432/db' \
    python manage.py collectstatic --noinput

USER app
EXPOSE 8000

ENTRYPOINT ["/srv/openatsc3-ca/deploy/entrypoint.sh"]
