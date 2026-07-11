"""On-disk RS256 keypair management for the embedded OIDC issuer.

Keys live under ``AUTH_LOCAL_KEYS_DIR`` (default ``${DATA_DIR}/auth_keys/``).
On first init the keystore generates a 4096-bit RSA keypair; subsequent boots
load all keypairs and sign with the newest. JWKS exposes every public key so
tokens signed by older keys remain verifiable across rotations.

File layout:
    {kid}.pem      — PKCS8 private key, mode 0600
    {kid}.pub.pem  — SubjectPublicKeyInfo public key

``kid`` is the first 16 hex of SHA-256 of the public key DER. Stable across
processes for the same keypair.

Multi-replica note: this implementation assumes a shared filesystem when
``WEB_CONCURRENCY > 1``. The boot guard in ``main.py`` refuses to start when
that invariant is violated. DB-backed key storage is Phase 11 future work.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from authlib.jose import JsonWebKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

log = logging.getLogger(__name__)

DEFAULT_KEY_BITS = 4096


@dataclass
class KeyEntry:
    kid: str
    private_pem: bytes
    public_pem: bytes
    created_at: datetime


class Keystore:
    """Loads keypairs from disk, generates one if the directory is empty.

    Construction is the only side-effecting operation; downstream callers use
    ``current_private_pem()`` to sign tokens and ``jwks_dict()`` to publish
    public keys at ``/.well-known/jwks.json``.
    """

    def __init__(self, keys_dir: str | os.PathLike[str], key_bits: int = DEFAULT_KEY_BITS) -> None:
        self._dir = Path(keys_dir)
        self._key_bits = key_bits
        self._lock = threading.Lock()
        self._keys: list[KeyEntry] = []
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_or_generate()

    # -------- loading & generating --------

    def _load_or_generate(self) -> None:
        for priv_path in sorted(self._dir.glob("*.pem")):
            if priv_path.name.endswith(".pub.pem"):
                continue
            kid = priv_path.stem
            pub_path = self._dir / f"{kid}.pub.pem"
            if not pub_path.exists():
                log.warning("Keystore: orphan private key %s without matching public; skipping", priv_path)
                continue
            self._keys.append(
                KeyEntry(
                    kid=kid,
                    private_pem=priv_path.read_bytes(),
                    public_pem=pub_path.read_bytes(),
                    created_at=datetime.fromtimestamp(priv_path.stat().st_mtime, tz=timezone.utc),
                )
            )

        if not self._keys:
            self._generate_new()

        log.info(
            "Keystore: ready with %d keypair(s) in %s; current kid=%s",
            len(self._keys), self._dir, self.current_kid(),
        )

    def _generate_new(self) -> KeyEntry:
        priv = rsa.generate_private_key(public_exponent=65537, key_size=self._key_bits)
        priv_pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        pub_der = priv.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        kid = hashlib.sha256(pub_der).hexdigest()[:16]

        priv_path = self._dir / f"{kid}.pem"
        pub_path = self._dir / f"{kid}.pub.pem"
        priv_path.write_bytes(priv_pem)
        try:
            os.chmod(priv_path, 0o600)
        except OSError:
            pass
        pub_path.write_bytes(pub_pem)

        entry = KeyEntry(
            kid=kid,
            private_pem=priv_pem,
            public_pem=pub_pem,
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._keys.append(entry)
        log.info("Keystore: generated new keypair kid=%s", kid)
        return entry

    # -------- public API --------

    def _current_entry(self) -> KeyEntry:
        if not self._keys:
            raise RuntimeError("Keystore has no keys loaded")
        return max(self._keys, key=lambda k: k.created_at)

    def current_kid(self) -> str:
        return self._current_entry().kid

    def current_private_pem(self) -> bytes:
        return self._current_entry().private_pem

    def jwks_dict(self) -> dict[str, Any]:
        keys: list[dict[str, Any]] = []
        for entry in self._keys:
            jwk = JsonWebKey.import_key(
                entry.public_pem,
                {"kty": "RSA", "use": "sig", "kid": entry.kid, "alg": "RS256"},
            ).as_dict()
            keys.append(jwk)
        return {"keys": keys}

    def rotate(self) -> str:
        """Generate a new keypair, making it the current signer. Old keys remain in JWKS."""
        return self._generate_new().kid

    def all_kids(self) -> list[str]:
        return [k.kid for k in sorted(self._keys, key=lambda x: x.created_at)]
