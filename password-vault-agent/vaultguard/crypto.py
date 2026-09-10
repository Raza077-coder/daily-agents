"""VAULTGUARD cryptography layer.

Key derivation
    PBKDF2-HMAC-SHA256, pure standard library, configurable iteration count
    (default 200,000 — OWASP 2023 guidance for PBKDF2-HMAC-SHA256).

Authenticated encryption
    ``aes-256-gcm``          preferred, used automatically when the optional
                             ``cryptography`` package is importable.
    ``hmac-sha256-ctr-etm``  pure-stdlib fallback. A keystream is produced with
                             HMAC-SHA256 in counter mode and the payload is
                             authenticated with a separate HMAC-SHA256 key
                             using Encrypt-then-MAC over (aad ‖ nonce ‖ ct).

Both constructions are AEAD: flipping any bit of the nonce, ciphertext or
associated data makes :func:`decrypt` raise :class:`VaultCryptoError` rather
than returning corrupted plaintext.

>>> key, salt = derive_key("correct horse", os.urandom(16))
>>> blob = encrypt(key, b"top secret", aad=b"entry-1")
>>> decrypt(key, blob, aad=b"entry-1")
b'top secret'
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import struct
from typing import Any, Dict, Optional, Tuple

PBKDF2 = "pbkdf2-hmac-sha256"
AES_GCM = "aes-256-gcm"
HMAC_CTR_ETM = "hmac-sha256-ctr-etm"

DEFAULT_ITERATIONS = 200_000
MIN_ITERATIONS = 10_000
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 16
TAG_BYTES = 32
BLOCK_BYTES = 32

_ENC_LABEL = b"vaultguard|enc|"
_MAC_LABEL = b"vaultguard|mac|"
_VERIFY_TOKEN = b"vaultguard-verify-v1"

try:  # optional, standards-compliant hardware-accelerated cipher
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

    HAS_AES_GCM = True
except Exception:  # pragma: no cover - exercised only without the dep
    _AESGCM = None  # type: ignore[assignment]
    HAS_AES_GCM = False


class VaultCryptoError(Exception):
    """Raised when a payload cannot be authenticated or decrypted."""


class VaultKdfError(Exception):
    """Raised when key-derivation parameters are invalid."""


# --------------------------------------------------------------------------- #
# encoding helpers
# --------------------------------------------------------------------------- #
def b64e(raw: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64d(text: str) -> bytes:
    """Decode URL-safe base64, tolerating missing padding."""
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


# --------------------------------------------------------------------------- #
# key derivation
# --------------------------------------------------------------------------- #
def derive_key(
    master_password: str,
    salt: Optional[bytes] = None,
    iterations: int = DEFAULT_ITERATIONS,
    length: int = KEY_BYTES,
) -> Tuple[bytes, bytes]:
    """Derive a vault key from ``master_password``.

    Returns ``(key, salt)``. When ``salt`` is ``None`` a fresh 16-byte salt is
    generated — always store the salt alongside the vault, it is not secret but
    it is required to reproduce the key.
    """
    if not isinstance(master_password, str) or not master_password:
        raise VaultKdfError("master password must be a non-empty string")
    if iterations < MIN_ITERATIONS:
        raise VaultKdfError(
            f"iteration count {iterations} is below the {MIN_ITERATIONS} minimum"
        )
    if salt is None:
        salt = os.urandom(SALT_BYTES)
    if len(salt) < SALT_BYTES:
        raise VaultKdfError(f"salt must be at least {SALT_BYTES} bytes")
    key = hashlib.pbkdf2_hmac("sha256", master_password.encode("utf-8"), salt, iterations, length)
    return key, salt


def hash_password_hint(password: str, salt: Optional[bytes] = None) -> str:
    """Deterministic, non-reversible fingerprint used for reuse detection.

    Two entries sharing a password share a hint, which lets the auditor spot
    duplicate credentials without ever storing the plaintext twice.
    """
    if salt is None:
        salt = b"vaultguard-hint-v1"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000, 16)
    return b64e(digest)


def default_cipher(force_stdlib: bool = False) -> str:
    """Pick the strongest cipher available on this interpreter."""
    if force_stdlib or not HAS_AES_GCM:
        return HMAC_CTR_ETM
    return AES_GCM


# --------------------------------------------------------------------------- #
# stdlib AEAD: HMAC-SHA256 counter-mode stream + encrypt-then-MAC
# --------------------------------------------------------------------------- #
def _xor_keystream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """XOR ``data`` with an HMAC-SHA256 counter-mode keystream."""
    if not data:
        return b""
    out = bytearray(len(data))
    counter = 0
    pos = 0
    while pos < len(data):
        block = hmac.new(key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest()
        chunk = data[pos : pos + BLOCK_BYTES]
        for i, byte in enumerate(chunk):
            out[pos + i] = byte ^ block[i]
        pos += len(chunk)
        counter += 1
    return bytes(out)


def _subkey(key: bytes, label: bytes, nonce: bytes) -> bytes:
    return hmac.new(key, label + nonce, hashlib.sha256).digest()


# --------------------------------------------------------------------------- #
# public AEAD API
# --------------------------------------------------------------------------- #
def encrypt(
    key: bytes,
    plaintext: bytes,
    *,
    cipher: Optional[str] = None,
    aad: bytes = b"",
    force_stdlib: bool = False,
) -> Dict[str, str]:
    """Authenticated encryption. Returns a JSON-serialisable blob."""
    if len(key) not in (16, 24, 32):
        raise VaultCryptoError("key must be 16, 24 or 32 bytes")
    cipher = cipher or default_cipher(force_stdlib)
    nonce = os.urandom(NONCE_BYTES)

    if cipher == AES_GCM:
        if not HAS_AES_GCM:  # pragma: no cover
            cipher = HMAC_CTR_ETM
        else:
            ct = _AESGCM(key).encrypt(nonce, plaintext, aad)
            return {"cipher": AES_GCM, "nonce": b64e(nonce), "ct": b64e(ct)}

    enc_key = _subkey(key, _ENC_LABEL, nonce)
    mac_key = _subkey(key, _MAC_LABEL, nonce)
    ct = _xor_keystream(enc_key, nonce, plaintext)
    tag = hmac.new(mac_key, aad + nonce + ct, hashlib.sha256).digest()
    return {
        "cipher": HMAC_CTR_ETM,
        "nonce": b64e(nonce),
        "ct": b64e(ct),
        "tag": b64e(tag),
    }


def decrypt(key: bytes, blob: Dict[str, str], *, aad: bytes = b"") -> bytes:
    """Verify and decrypt a blob produced by :func:`encrypt`."""
    if not isinstance(blob, dict):
        raise VaultCryptoError("vault payload is malformed")
    cipher = blob.get("cipher")
    try:
        nonce = b64d(blob["nonce"])
        ct = b64d(blob["ct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VaultCryptoError("vault payload is missing required fields") from exc

    if cipher == AES_GCM:
        if not HAS_AES_GCM:  # pragma: no cover
            raise VaultCryptoError("this vault needs the `cryptography` package to open")
        try:
            return _AESGCM(key).decrypt(nonce, ct, aad)
        except Exception as exc:
            raise VaultCryptoError("authentication failed — wrong key or tampered vault") from exc

    if cipher != HMAC_CTR_ETM:
        raise VaultCryptoError(f"unsupported cipher {cipher!r}")

    try:
        tag = b64d(blob["tag"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VaultCryptoError("vault payload is missing its authentication tag") from exc

    enc_key = _subkey(key, _ENC_LABEL, nonce)
    mac_key = _subkey(key, _MAC_LABEL, nonce)
    expected = hmac.new(mac_key, aad + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise VaultCryptoError("authentication failed — wrong key or tampered vault")
    return _xor_keystream(enc_key, nonce, ct)


def verification_blob(key: bytes, *, force_stdlib: bool = False) -> Dict[str, str]:
    """Encrypt a known token so the master password can be checked cheaply."""
    return encrypt(key, _VERIFY_TOKEN, aad=b"verify", force_stdlib=force_stdlib)


def check_verification_blob(key: bytes, blob: Dict[str, str]) -> bool:
    """Return ``True`` when ``key`` opens the verifier from the vault header."""
    try:
        return decrypt(key, blob, aad=b"verify") == _VERIFY_TOKEN
    except VaultCryptoError:
        return False


def constant_time_eq(left: str, right: str) -> bool:
    """Timing-safe string comparison helper."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def cipher_available(cipher: str) -> bool:
    """Whether ``cipher`` can be used by the running interpreter."""
    if cipher == AES_GCM:
        return HAS_AES_GCM
    return cipher == HMAC_CTR_ETM


def entropy_of_key(key: bytes) -> float:
    """Shannon entropy (bits/byte) of key material — a sanity/diagnostic check."""
    if not key:
        return 0.0
    counts = [0] * 256
    for byte in key:
        counts[byte] += 1
    total = len(key)
    value = 0.0
    for count in counts:
        if count:
            p = count / total
            value -= p * math.log2(p)
    return value


def bytes_to_hex(raw: bytes) -> str:
    """Hex helper used in logs and diagnostics."""
    return raw.hex()


def safe_compare_dict(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Compare two payload headers without leaking where they differ."""
    return constant_time_eq(str(sorted(left.items())), str(sorted(right.items())))
