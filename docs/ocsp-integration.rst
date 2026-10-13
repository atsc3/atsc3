Integrating OpenATSC3 signing + OCSP into your broadcast
=========================================================

How a broadcaster carries the OpenATSC3 signed signaling — the CertificationData
(``0x06``) with its **stapled OCSP** responses and the signed SignedMultiTable
(``0x07``) — on air, and how to keep it fresh.  You do **not** run certificate
status infra; the managed service signs and staples, and hands you the exact LLS
table bodies to transmit.

.. contents:: On this page
   :local:

The model: we staple, you carry
-------------------------------

.. important::

   **We do not operate an OCSP responder, and it is not supported.**  There is no
   network endpoint to query, and our certificates carry **no OCSP URL**
   (Authority Information Access) — so standard broadcast equipment cannot
   self-staple.  Instead the service gives you the **already-stapled** OCSP
   responses, embedded in the CertificationData; your equipment just carries
   them.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Task
     - Who
   * - Sign OCSP responses, staple one per certificate into the CDT ``0x06``
     - **The service** (OpenATSC3)
   * - Wrap the returned ``0x06``/``0x07`` into LLS and transmit
     - **You** (your broadcast equipment)
   * - Keep the stapled responses fresh (they expire)
     - **You** (re-fetch on a schedule)

You submit your LLS table bodies; the service returns signed tables.  The OCSP
status for every certificate in the chain is computed and stapled **server-side**
(`services.publish` builds one response per certificate, each authorized by its
issuer).  No OCSP request or responder runs at your station.

Prerequisites
-------------

* A broadcaster registered under your tenant (its name, e.g. ``WHUT``).
* A tenant **API token** (``Authorization: Token <t>`` or ``X-API-Token``).

Endpoints
----------

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Method / path
     - Purpose
   * - ``POST /api/v1/smt/``
     - Sign your LLS tables; returns ``0x06`` + ``0x07`` bodies
   * - ``GET /api/v1/tenant/``
     - Your tenant and its broadcaster names
   * - ``GET /api/v1/certificates/``
     - Your certificates (read-only)

Request and response
--------------------

Request body (JSON):

.. code-block:: json

   {
     "broadcaster": "WHUT",
     "tables": [
       {"table_id": 1,   "version": 1, "payload_b64": "<base64 LLS body>"},
       {"table_id": 3,   "version": 1, "payload_b64": "<base64 LLS body>"}
     ]
   }

``tables`` are the LLS table bodies you want signed (``table_id`` may be decimal
or hex; ``payload_b64`` is the raw body, base64-encoded).  Response (JSON):

.. code-block:: json

   {
     "cdt_b64": "<base64 of the gzipped CertificationData 0x06 body>",
     "smt_b64": "<base64 of the SignedMultiTable 0x07 body>"
   }

* ``cdt_b64`` decodes to the **gzipped CDT XML** — the LLS ``0x06`` body **with
  the stapled ``<OCSPResponse>`` elements already inside**.  Keep it gzipped;
  that is what goes on air.
* ``smt_b64`` decodes to the CMS ``SignedData`` over the tables you submitted —
  the LLS ``0x07`` body.

.. code-block:: bash

   curl -sS https://ca.example.com/api/v1/smt/ \
     -H "Authorization: Token $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"broadcaster":"WHUT","tables":[{"table_id":1,"version":1,
          "payload_b64":"'"$(base64 -w0 slt.xml.gz)"'"}]}'

Errors are JSON ``{"error": "..."}``:

.. list-table::
   :header-rows: 1
   :widths: 12 88

   * - Status
     - Meaning
   * - ``400``
     - invalid JSON, a bad table entry, or no tables supplied
   * - ``401``
     - missing/invalid/expired API token
   * - ``404``
     - no broadcaster with that name for your tenant
   * - ``413``
     - request body too large
   * - ``429``
     - per-tenant rate limit exceeded

.. _ocsp-carriage:

Putting it on the air
---------------------

Each LLS table goes on the wire as a 4-byte header followed by the body:

.. code-block:: text

   [table_id, 0, 0, version] ++ body

so wrap the decoded bodies as:

* ``0x06`` body = the gzipped CDT (``cdt_b64`` decoded), with the SLT ``0x01``;
* ``0x07`` body = the signed SMT (``smt_b64`` decoded).

Emit the ``0x01``/``0x06``/``0x07`` LLS tables in your broadcast (A/331 §6.7,
A/360 §5.2.2.2).  The receiver parses the CDT, walks each certificate chain to
your trusted root, and checks the **stapled** OCSP response for every cert —
offline, from the broadcast, without any query to us.

Refresh contract
----------------

The stapled responses expire, so **re-fetch on a schedule**:

* ``OCSPRefresh`` is carried in the CDT; the service's default is **20 hours**.
* Each response's ``nextUpdate`` is ``thisUpdate + 20 h``; the absolute ceiling a
  receiver accepts is **10 days**.
* Therefore **re-fetch and re-broadcast at least daily** (sooner if your
  maintenance window is wider than the window).  A stale response makes the
  receiver **reject** the tables.

The recommended loop is a scheduled job that re-runs the fetch below (e.g. every
6–20 hours) and swaps the new ``0x06``/``0x07`` tables into the broadcast.

Helper script
-------------

``openatsc3-ca/tools/openatsc3-smt-fetch.py`` (stdlib-only) does the whole client
side — build the request, POST, decode, and write LLS-ready files:

.. code-block:: bash

   python openatsc3-smt-fetch.py \
       --base-url https://ca.example.com --token "$TOKEN" \
       --broadcaster WHUT \
       --table 0x01:slt.xml.gz --table 0x03:app.bin \
       --out-dir out/signed

It writes ``cdt.xml.gz``, ``signing.smt``, and the LLS-wrapped
``lls-0x06.bin`` / ``lls-0x07.bin`` ready to feed your exciter.  ``--table`` takes
``TABLE_ID[:VERSION]:PATH``.  Run it on the refresh schedule above.

Revocation and rollover
-----------------------

* **Revocation.**  When a certificate is revoked, the next fetch returns a fresh
  ``revoked`` status for it; a receiver then rejects signaling that depends on it
  once the previously-valid response ages out (bounded by ``OCSPRefresh`` and the
  10-day ceiling).
* **Rollover.**  During a key rollover the CDT carries the NextCert in a
  ``CertReplacement`` element with its window; keep fetching over the rollover so
  the transition stays verifiable end to end.

Alternative: the delivered bundle
---------------------------------

Where you receive a *bundle* (e.g. an order delivery) rather than calling the API,
it contains ``certs.pem`` (the CDT's certificate set), ``cdt.xml.gz``,
``ocsp-NN.der`` (each stapled response separately), ``signing.smt``, and
``crl.pem``.  The stapled OCSP inside ``cdt.xml.gz`` is the same mechanism; the
per-certificate ``ocsp-NN.der`` files are informational.

Troubleshooting
---------------

* **Receiver rejects after a while** — the stapled responses went stale; re-fetch
  (see the refresh contract).
* **``401``** — the token is missing, invalid, or expired.
* **``429``** — you are fetching too often for the rate limit; back off
  (the refresh cadence does not need to be aggressive).
* **``413``** — the request body is too large; the size cap is per request.

See also
--------

* :doc:`ca-operator-runbook` — the certificate authority and its ceremonies.
* :doc:`pki-transmission` — the CA/transmit/receive walkthrough.
* ``openatsc3-ca/README.md`` — the app's commands and API reference.
