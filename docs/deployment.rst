Deploying the CA app (AWS)
==========================

How the ``openatsc3-ca`` application is containerized and released.  The
**Terraform infrastructure lives in the sibling ``../atsc3certs`` repository**
(``deploy/terraform``); this page covers the app side and points there for the
full run procedure.

.. important::

   The offline root is **not** part of this stack — it is generated air-gapped
   and its public anchor is imported by ceremony (see
   :doc:`ca-operator-runbook`).  Terraform provisions the online service only.

Stack and layout
----------------

The all-AWS L2 stack (VPC, RDS Multi-AZ, ECS Fargate ×2 + ALB, S3, Secrets,
ECR, IAM) is Terraform, applied from ``atsc3certs``.  This repository holds:

.. code-block:: text

   atsc3/
     openatsc3-ca/
       Dockerfile                     # build context is the repo root
       deploy/entrypoint.sh           # export the PEM tree, then gunicorn
     .github/workflows/deploy.yml     # build → ECR → migrate task → roll service

The container installs the local ``openatsc3-pki`` and ``openatsc3-ca[kms,
stripe,aws,prod]`` from the checkout (the context must be the monorepo root),
runs as a non-root user, and serves with gunicorn.  On start it rebuilds the
PEM tree from the database (``manage.py export``); private keys are KMS, so the
container is stateless.

Deploy
------

The full, authoritative procedure (bootstrap, environment apply, image, first-run
setup, KMS keys, HTTPS/Stripe) is in the other repository:
``../atsc3certs/docs/deployment.rst``.

In short:

#. Apply the Terraform stack from ``../atsc3certs/deploy/terraform`` (bootstrap,
   then ``environments/dev``) to create ECR, RDS, ECS, the ALB, and the secrets.
#. Push to ``main`` here — ``deploy.yml`` builds the image, runs the **migrate**
   one-off ECS task, and rolls the service (OIDC, no static keys).
#. Run the one-time app setup (``migrate``, ``createsuperuser``, ``import-signed
   --profile root``, the responder ceremony).

The ``tf-plan.yml`` (format/validate/plan on PRs) lives with the Terraform in
``atsc3certs``; apply is manual.

See also
--------

* :doc:`ca-operator-runbook` — the certificate ceremonies and rotation.
* :doc:`pki-transmission` — the CA/transmit/receive walkthrough.
* ``../atsc3certs/docs/deployment.rst`` — the deploy run procedure.
