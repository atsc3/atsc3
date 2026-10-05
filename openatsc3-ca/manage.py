#!/usr/bin/env python
"""Django management entry point for the OpenATSC3 CA app.

    python manage.py migrate
    python manage.py createsuperuser
    python manage.py runserver

Run ``manage.py`` with a Python 3.12 environment that has Django installed.
The crypto library (``openatsc3_pki``) itself needs no Django.
"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openatsc3_ca.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django is not installed.  Install the package: "
            "pip install -e 'openatsc3-ca'"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
