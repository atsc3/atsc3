"""The OpenATSC3 CA catalog: the PostgreSQL ledger of the certificate lifecycle.

This app is the operator/system-of-record layer over the ``openatsc3_pki``
crypto library.  It contains **no cryptography**: it stores issued certificates,
their status, OCSP responses, rollover plans and an audit trail, and calls into
``openatsc3_pki`` to issue, revoke and sign.  The database is the source of
truth; the PEM tree is materialized by the ``export`` command.
"""
