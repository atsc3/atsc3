CA operator runbook: air-gapped root and tenant lifecycle
=========================================================

This is the operator procedure for running the ``openatsc3-ca`` service with an
**air-gapped root held as a 3-of-5 quorum**.  The root key never touches the
online host: tenant-intermediate CAs and the root-delegated OCSP responder are
created by a *ceremony* (request online, sign air-gapped, import online).

.. important::

   The root key is **not** an AWS KMS key.  KMS asymmetric keys are single-key
   with no n-of-m, which is exactly the single point a terminal-if-compromised
   root must avoid.  The root is split with Shamir secret sharing and any 3 of 5
   custodians can sign at a ceremony.

.. note::

   This runbook is version-controlled (reStructuredText, built by ``make docs``).
   The design *record* lives in the project wiki (``atsc3certs/wiki``), which is
   not tracked: see the mechanism page listed under **See also**.

.. contents:: On this page
   :local:

Roles and controls
------------------

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Role
     - Responsibility
   * - Ceremony Master
     - Runs the commands, reads back results for the record.
   * - Custodian (×5)
     - Holds exactly **one** share; presents it when required.  No person holds
       more than one share.
   * - Witness (≥2)
     - Observes; attests to each step.  A custodian never witnesses their own
       action.
   * - Recorder
     - Keeps the minutes, hashes, and the share-receipt log.
   * - Security Officer
     - Owns the air-gapped host, media, and storage; may veto.

Controls for every ceremony:

* Air-gapped host: no network, no Bluetooth; clean, prepared removable media.
* The tool version is pinned and verified (record the ``openatsc3-pki`` /
  ``openatsc3-ca`` git commit).
* Every step is witnessed; shares stay in tamper-evident containers.
* A custodian's share is only ever used on the air-gapped host, in memory.

Setting the online app for ceremony-only issuance
-------------------------------------------------

Set ``OPENATSC3_CA_ROOT_OFFLINE=1`` on the online ``openatsc3-ca`` host.  With it
set, ``issue-tenant-ca``, ``issue-root-responder``, and ``fulfill_order`` refuse
to touch a local root key and instead point at this ceremony:

.. code-block:: bash

   export OPENATSC3_CA_ROOT_OFFLINE=1

Ceremony A — root generation and 3-of-5 split (one-time)
--------------------------------------------------------

On the air-gapped host, in a scratch directory shipped with the tooling:

.. code-block:: bash

   openatsc3-pki init-root --dir ca --cn "OpenATSC3 Root CA"
   openatsc3-pki show --dir ca            # verify subject, P-384, validity

Confirm the root certificate fields against the minutes (subject, serial,
SHA-1 SubjectKeyIdentifier, not-after, curve ``secp384r1``).  Split the key:

.. code-block:: bash

   openatsc3-pki quorum-split --key ca/root/root.key.pem \
       --threshold 3 --shares 5 --out-dir custody/shares

``quorum-split`` self-checks that a quorum reconstructs before writing; if the
check fails it aborts.  Then:

#. Give each custodian one ``share-N`` file; record each share's SHA-256 in the
   share-receipt log.
#. **Destroy the monolithic key media** (``ca/root/root.key.pem``) by overwriting
   and physically destroying it, only after a 3-of-5 reconstruction is verified.
#. Carry **only the public anchor** (``ca/root/root.cert.pem``) back to the online
   host and register it:

.. code-block:: bash

   python manage.py import-signed --cert root.cert.pem --profile root

That records the trust anchor in the ledger and materializes
``root/root.cert.pem`` where the online request flow reads it.

Ceremony B — tenant CA issuance (customer onboarding)
-----------------------------------------------------

A customer's tenant intermediate CA is signed by the root, so it is created by a
ceremony, **before** the order is approved.  Minting several tenant CAs in one
ceremony amortizes the effort.

Online — create the subject key and the request:

.. code-block:: bash

   # tenant CA key in KMS (recommended) ...
   aws kms create-key --key-spec ECC_NIST_P384 --key-usage SIGN_VERIFY
   python manage.py request-tenant-ca --tenant acme --kms-key-id <arn> \
       --out acme.req.der
   # ... or a local key under tenants/acme/ when KMS is not used
   python manage.py request-tenant-ca --tenant acme --out acme.req.der

Air-gapped — three custodians present their shares; review the request, then sign:

.. code-block:: bash

   openatsc3-pki sign-request --request acme.req.der \
       --shares custody/shares/share-1 custody/shares/share-3 custody/shares/share-5 \
       --out acme.pem

Fewer than three shares fail (a quorum is required).  Online — record the result:

.. code-block:: bash

   python manage.py import-signed --cert acme.pem --profile tenant-ca --tenant acme

The chain links automatically (the request carried the root's AuthorityKey
Identifier).  Verify the parent is the root and the profile is ``tenant-ca`` in
the admin.  The customer's order can now be approved; ``fulfill_order`` reuses the
pre-provisioned tenant CA (it no longer issues one itself when the root is
offline) and signs the leaves with the tenant CA key.

Ceremony C — root-delegated revocation responder (one-time, then rotate)
------------------------------------------------------------------------

The root also authorizes one online **revocation responder**, which signs the
OCSP responses for tenant CAs (a CA's OCSP must come from its issuer).  It must
exist before any ``publish``.

.. code-block:: bash

   python manage.py request-root-responder --out resp.req.der
   openatsc3-pki sign-request --request resp.req.der \
       --shares custody/shares/share-1 custody/shares/share-3 custody/shares/share-5 \
       --out resp.pem
   python manage.py import-signed --cert resp.pem \
       --profile ocsp-responder --responder

Rotate it before its certificate expires (Ceremony C repeats).  Its compromise
can suppress or deny revocation but cannot mint certificates.

Ceremony D — revoking a tenant CA (emergency backstop)
------------------------------------------------------

Normal tenant-CA revocation is published online by the revocation responder.  If
that path is unavailable, a fresh ``revoked`` OCSP for the tenant CA can be
produced by a ceremony and stapled.  Invoke only on instruction from the
Security Officer; record the reason.

Ceremony E — custodian change / share re-split
----------------------------------------------

To onboard/offboard a custodian or replace a compromised share, a quorum
reconstructs the key on the air-gapped host and re-splits it:

.. code-block:: bash

   openatsc3-pki quorum-combine --shares <3 shares> --out root.key.pem
   openatsc3-pki quorum-split --key root.key.pem \
       --threshold 3 --shares 5 --out-dir custody/new-shares
   # destroy root.key.pem and the superseded shares after the self-check

Treat the reconstruction as a full ceremony; the reconstructed key exists only in
memory and on ephemeral media that must be destroyed.

Disaster recovery
-----------------

* **Fewer than 3 shares available:** the root cannot sign; treat as a root loss
  and rotate (below).  There is no way to recover below threshold by design.
* **Air-gapped host lost:** rebuild a clean host and use the shares; no key
  material lived on the host.
* **Root key believed compromised:** stop ceremonies, rotate the root (below),
  and revoke the tenant CAs as needed.  Assume a hardcoded consumer trust
  anchor cannot be changed in the field.

Root rotation (future)
----------------------

Root rotation requires generating a new root, cross-signing under the old root
where possible, and distributing the new anchor to every trust store.  For
hardcoded consumer receivers this is not generally possible; rotation is
therefore a last resort and is tracked as a Phase 4 item.

Evidence and retention
----------------------

For each ceremony keep: the pinned tool commit, request and result hashes, the
certificate serial/SKI, which shares were used, the witness attestations, and the
share-receipt log.  Audit events are appended by the app
(``request-*``, ``import-signed``, ``publish``).

Command map
-----------

.. list-table::
   :header-rows: 1
   :widths: 46 54

   * - Step
     - Command
   * - Generate root / inspect
     - ``openatsc3-pki init-root`` / ``openatsc3-pki show``
   * - Split / recombine quorum
     - ``openatsc3-pki quorum-split`` / ``quorum-combine``
   * - Register the anchor
     - ``python manage.py import-signed --profile root``
   * - Request tenant CA / responder
     - ``python manage.py request-tenant-ca`` / ``request-root-responder``
   * - Air-gapped sign
     - ``openatsc3-pki sign-request`` (``--key`` or ``--shares``)
   * - Import signed result
     - ``python manage.py import-signed``
   * - Approve the customer
     - ``python manage.py approve-order`` (or the admin action)

Known gaps
----------

* No ``quorum-reshare``, ``root-rotate``, or signed-minutes tooling; Ceremonies D
  and E and rotation are procedural.
* ``OPENATSC3_CA_ROOT_OFFLINE`` must be set explicitly; without it the online app
  would try to sign with a local root key (present only in test/dev).

See also
--------

* :doc:`pki-transmission` — the library-level CA and signed-frame round trip.
* The project wiki (``atsc3certs/wiki``): the offline-root-ceremony mechanism,
  the root-compromise threat model, and the root-custody decision.
