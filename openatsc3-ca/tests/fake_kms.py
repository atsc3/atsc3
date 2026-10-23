"""A minimal in-memory KMS client for custody tests (no boto3 dependency)."""

from __future__ import annotations

import secrets
from typing import Dict

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from openatsc3_pki import keys

from openatsc3_ca.catalog import custody

_SPEC_CURVES = {
    "ECC_NIST_P256": "secp256r1",
    "ECC_NIST_P384": "secp384r1",
    "ECC_NIST_P521": "secp521r1",
}
_ALGORITHMS = {
    "ECDSA_SHA_256": hashes.SHA256,
    "ECDSA_SHA_384": hashes.SHA384,
    "ECDSA_SHA_512": hashes.SHA512,
}


class FakeKms:
    def __init__(self) -> None:
        self.keys: Dict[str, dict] = {}
        self.aliases: Dict[str, str] = {}
        self.wrapped: Dict[bytes, bytes] = {}
        self._counter = 0

    def _new_id(self) -> str:
        self._counter += 1
        return f"key-{self._counter}"

    def _arn(self, key_id: str) -> str:
        return f"arn:aws:kms:us-east-1:111122223333:key/{key_id}"

    def _resolve(self, key_id: str) -> str:
        if key_id in self.aliases:
            return self.aliases[key_id]
        if key_id in self.keys:
            return key_id
        for arn in self.keys:
            if arn.endswith("/" + key_id):
                return arn
        raise custody._KeyNotFound(key_id)

    def create_key(self, KeyUsage, KeySpec, Description="", Tags=None, **kwargs):
        key_id = self._new_id()
        arn = self._arn(key_id)
        if KeyUsage == "SIGN_VERIFY":
            curve = _SPEC_CURVES[KeySpec]
            private = keys.generate(curve).private
        else:
            private = None
        self.keys[arn] = {"private": private, "spec": KeySpec,
                          "usage": KeyUsage, "arn": arn, "id": key_id}
        return {"KeyMetadata": {"KeyId": key_id, "Arn": arn,
                                "KeySpec": KeySpec, "KeyUsage": KeyUsage}}

    def create_alias(self, AliasName, TargetKeyId):
        if AliasName in self.aliases:
            raise custody._AliasExists(AliasName)
        self.aliases[AliasName] = self._resolve(TargetKeyId)

    def describe_key(self, KeyId):
        arn = self._resolve(KeyId)
        meta = self.keys[arn]
        return {"KeyMetadata": {"KeyId": meta["id"], "Arn": arn,
                                "KeySpec": meta["spec"],
                                "KeyUsage": meta["usage"]}}

    def get_public_key(self, KeyId):
        meta = self.keys[self._resolve(KeyId)]
        public = meta["private"].public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        return {"PublicKey": public, "KeySpec": meta["spec"],
                "KeyUsage": meta["usage"]}

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        meta = self.keys[self._resolve(KeyId)]
        algorithm = _ALGORITHMS[SigningAlgorithm]()
        if MessageType == "DIGEST":
            signature = meta["private"].sign(
                Message, ec.ECDSA(utils.Prehashed(algorithm)))
        else:
            signature = meta["private"].sign(Message, ec.ECDSA(algorithm))
        return {"Signature": signature}

    def generate_data_key_pair(self, KeyId, KeyPairSpec):
        self._resolve(KeyId)
        curve = _SPEC_CURVES[KeyPairSpec]
        private = keys.generate(curve)
        plaintext = private.private.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        public = private.public.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        ciphertext = secrets.token_bytes(48)
        self.wrapped[ciphertext] = plaintext
        return {"PrivateKeyCiphertextBlob": ciphertext,
                "PrivateKeyPlaintext": plaintext,
                "PublicKey": public,
                "KeyPairSpec": KeyPairSpec,
                "KeyId": KeyId}

    def decrypt(self, KeyId, CiphertextBlob, EncryptionContext=None):
        self._resolve(KeyId)
        return {"Plaintext": self.wrapped[CiphertextBlob], "KeyId": KeyId}
