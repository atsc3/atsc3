"""ATSC A/360 registered object identifiers and certificate-profile constants.

Every constant here is normative and names its A/360 source.  The ATSC private
enterprise arc is ``1.3.6.1.4.1.51552`` (``id-atsc``); the values below are
Table A.1.  The signing-signer certificate profile is A/360 5.3.1.6, the root
profile 5.3.1.2, the CA profile 5.3.1.3 and the OCSP responder profile 5.3.1.7.
"""

#: A/360 Annex A: ATSC IANA Private Enterprise Number arc (``id-atsc``).
ATSC_PEN = "1.3.6.1.4.1.51552"

#: A/360 Table A.1: broadcast signaling signing key purpose.
ID_ATSC_KP_SIGNALING_SIGNING = ATSC_PEN + ".37.3"

#: A/360 Table A.1: application author key purpose.
ID_ATSC_KP_AUTHOR = ATSC_PEN + ".37.1"

#: A/360 Table A.1: application distributor key purpose.
ID_ATSC_KP_DISTRIBUTOR = ATSC_PEN + ".37.2"

#: A/360 Table A.1: Subject Directory Attribute holding the broadcast stream
#: identifiers (a SET OF INTEGER).
ID_ATSC_SDATTR_BSID = ATSC_PEN + ".9.1"

#: A/360 Table A.2: OCSP signing key purpose (RFC 6960).
ID_KP_OCSP_SIGNING = "1.3.6.1.5.5.7.3.9"

#: A/360 Table A.2: code signing key purpose (RFC 5280).
ID_KP_CODE_SIGNING = "1.3.6.1.5.5.7.3.3"

#: A/360 5.2.2.2: LLS table id of the CertificationData table.
CERTIFICATION_DATA_LLS_TABLE_ID = 0x06

#: A/331 6.7: LLS table id of the SignedMultiTable.
SIGNED_MULTI_TABLE_LLS_TABLE_ID = 0x07

#: A/360 5.3.1.2: minimum ECDSA key size for a root certificate, bits.
ROOT_ECDSA_MIN_BITS = 384

#: A/360 5.3.1.3: minimum ECDSA key size for an issuing CA, bits.
CA_ECDSA_MIN_BITS = 256

#: A/360 5.3.1.6: minimum ECDSA key size for a signaling signer, bits.
SIGNER_ECDSA_MIN_BITS = 256

#: A/360 5.2.2.1 (item 4): the only permitted SignatureAlgorithm / digest
#: pairs, as (cryptography signature hash name, curve name or None).
SIGNATURE_ALGORITHMS = (
    ("sha256", "secp256r1"),
    ("sha384", "secp384r1"),
    ("sha512", "secp521r1"),
)

#: A/360 5.2.2.6 item 5: an OCSP response is stale if now - producedAt exceeds
#: this many days.
OCSP_MAX_AGE_DAYS = 10

#: A/360 5.2.2.6 item 5: clock-skew allowance on producedAt, hours.
OCSP_SKEW_HOURS = 1
