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
reconstructs the key on the air-gapped host and re-splits it **in memory** —
``quorum-reshare`` never writes the whole key to disk:

.. code-block:: bash

   # self-check first (reconstructs a valid key, writes nothing)
   openatsc3-pki quorum-verify --shares <3 shares>

   openatsc3-pki quorum-reshare --shares <3 shares> \
       --threshold 3 --shares-out 5 --out-dir custody/new-shares
   # self-check runs and aborts (nothing written) if the shares are < threshold

Then destroy the superseded shares.  Treat the reconstruction as a full
ceremony; any reconstructed key media is ephemeral and must be destroyed.
``quorum-combine`` (writes the whole key with 0600) is only for recovery/HSM
import, never for routine re-splitting.

Ceremony F — root rotation (last resort)
----------------------------------------

Root rotation issues a new root and makes it verifiable to anyone who still
trusts only the outgoing anchor by **cross-signing** the new root's key under the
old root.  On the air-gapped host, with the new root's self-signed certificate
and the old root's certificate:

.. code-block:: bash

   openatsc3-pki cross-sign --new-root new-root.cert.pem \
       --old-root old-root.cert.pem \
       --shares <3 shares> --out new-root-cross.pem

Assemble the distribution bundle (the new anchor, the bridge, and a hashed
manifest) for the teams that operate trust stores:

.. code-block:: bash

   openatsc3-pki rotation-bundle --new-root new-root.cert.pem \
       --cross new-root-cross.pem --old-root old-root.cert.pem \
       --witness <name> --out-dir rotation-bundle

Online, the app keeps a **single active anchor**; the cutover to the new root is
the deliberate ``import-signed --profile root`` with ``new-root.cert.pem``, and
the bridge covers verifiers that still pin the old root during the transition.

The cross certificate carries the new root's subject and key but is issued by the
old root, so a verifier holding the old anchor can path-build *old root → cross
certificate → new-root-issued certificates* until the new anchor is distributed.
This does **not** change a hardcoded consumer trust anchor: if the receiver pins
the old root, rotation cannot reach it.  A full rotation therefore also requires
distributing the new self-signed anchor to every trust store (out of scope for
hardcoded receivers) and is tracked as a Phase 4 item.

The receiver-side procedure — add the new anchor, cut over, then retire the old
one (the "add → cut over → retire" overlap) — is in
:doc:`pki-transmission` ("Rotating the receiver trust anchor").

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

The **cross-signing bridge** is implemented (Ceremony F).  What remains is
generating and distributing the new self-signed anchor to every trust store; for
hardcoded consumer receivers this is not generally possible, so rotation stays a
last resort and is tracked as a Phase 4 item.

Evidence and retention
----------------------

For each ceremony keep: the pinned tool commit, request and result hashes, the
certificate serial/SKI, which shares were used, the witness attestations, and the
share-receipt log.  Record the transcript with ``minutes`` (binds the inputs and
outputs by SHA-256, optionally signed with ``--key``/``--shares``) and check it
with ``verify-minutes`` before filing:

.. code-block:: bash

   openatsc3-pki minutes --op sign-request \
       --artifact request=acme.req.der --artifact result=acme.pem \
       --cert issuer=root.cert.pem --witness <name> \
       --shares <3 shares> --out acme.minutes.json
   openatsc3-pki verify-minutes --minutes acme.minutes.json \
       --issuer root.cert.pem

Audit events are appended by the app (``request-*``, ``import-signed``,
``publish``).

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
     - ``openatsc3-pki quorum-split`` / ``quorum-combine`` / ``quorum-verify``
   * - Re-split (custodian change)
     - ``openatsc3-pki quorum-reshare``
   * - Register the anchor
     - ``python manage.py import-signed --profile root``
   * - Request tenant CA / responder
     - ``python manage.py request-tenant-ca`` / ``request-root-responder``
   * - Air-gapped sign
     - ``openatsc3-pki sign-request`` (``--key`` or ``--shares``)
   * - Cross-sign a new root
     - ``openatsc3-pki cross-sign``
   * - Assemble a rotation bundle
     - ``openatsc3-pki rotation-bundle``
   * - Record / verify minutes
     - ``openatsc3-pki minutes`` / ``verify-minutes``
   * - Import signed result
     - ``python manage.py import-signed``
   * - Approve the customer
     - ``python manage.py approve-order`` (or the admin action)

Known gaps
----------

* Root rotation is procedural beyond the cross-signing bridge and bundle
  (Ceremony F): the new anchor must still be distributed to trust stores, and a
  verifier that pins the old root hardcoded cannot be reached.
* The app keeps a single active root anchor; there is no dual-anchor transition
  state online (the bridge covers the transition on the verifier side).
* ``OPENATSC3_CA_ROOT_OFFLINE`` must be set explicitly; without it the online app
  would try to sign with a local root key (present only in test/dev).

See also
--------

* :doc:`pki-transmission` — the library-level CA and signed-frame round trip.
* The project wiki (``atsc3certs/wiki``): the offline-root-ceremony mechanism,
  the root-compromise threat model, and the root-custody decision.
