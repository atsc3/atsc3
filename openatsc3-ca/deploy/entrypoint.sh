#!/bin/sh
# Container entrypoint for the OpenATSC3 CA app.
#
# With no arguments (the web service): materialize the PEM tree from the
# database and serve with gunicorn.  The tree is a view of the ledger (private
# keys are KMS), so it is safe to rebuild on every task start.
#
# With arguments (the one-off migrate task, or a shell): run them as-is, e.g.
#   python manage.py migrate --noinput
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

python manage.py export

exec gunicorn openatsc3_ca.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile -
