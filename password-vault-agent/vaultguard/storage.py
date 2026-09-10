"""VAULTGUARD vault file format and atomic persistence.

On-disk layout (single JSON document):

.. code-block:: json

    {
      "format": "vaultguard",
      "version": 1,
      "kdf": {"algo": "pbkdf2-hmac-sha256", "iterations": 200000, "salt": "..."},
      "cipher": "hmac-sha256-ctr-etm",
      "verifier": {"cipher": "...", "nonce": "...", "ct": "...", "tag": "..."},
      "payload":  {"cipher": "...", "nonce": "...", "ct": "...", "tag": "..."},
      "meta": {"created_at": "...", "updated_at": "...", "entry_count": 3}
    }

The header (kdf parameters + cipher + verifier) is *not* encrypted so the vault
can be unlocked without parsing potentially hostile plaintext, and the header is
bound to the ciphertext via the AEAD associated data, so tampering with the salt
or iteration count is detected rather than silently honoured.

Writes are atomic: content goes to a sibling ``*.tmp`` file which is ``fsync``ed
and then ``os.replace``d over the target.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional

from .crypto import (
    DEFAULT_ITERATIONS,
    HMAC_CTR_ETM,
    PBKDF2,
    VaultCryptoError,
    b64d,
    b64e,
    check_verification_blob,
    decrypt,
    default_cipher,
    derive_key,
    encrypt,
    verification_blob,
)

VAULT_FORMAT = "vaultguard"
VAULT_VERSION = 1
DEFAULT_VAULT_PATH = os.path.join(os.path.expanduser("~"), ".vaultguard", "vault.json")
FILE_MODE = 0o600


class VaultFormatError(Exception):
    """Raised when a vault file is missing, malformed, or from a newer version."""


# --------------------------------------------------------------------------- #
# low-level helpers
# --------------------------------------------------------------------------- #
def vault_exists(path: str) -> bool:
    """Whether a vault file is present at ``path``."""
    return bool(path) and os.path.isfile(path)


def _atomic_write(path: str, text: str) -> None:
    """Write ``text`` to ``path`` atomically with restrictive permissions."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".vaultguard-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, FILE_MODE)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _header_bytes(header: Dict[str, Any]) -> bytes:
    """Canonical serialisation of the parts that get authenticated."""
    material = {
        "format": header.get("format"),
        "version": header.get("version"),
        "kdf": header.get("kdf"),
        "cipher": header.get("cipher"),
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_vault_header(path: str) -> Dict[str, Any]:
    """Read and validate the unencrypted header without deriving any key."""
    if not vault_exists(path):
        raise VaultFormatError(f"no vault found at {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise VaultFormatError(f"vault file is unreadable: {exc}") from exc

    if document.get("format") != VAULT_FORMAT:
        raise VaultFormatError("not a VAULTGUARD vault")
    if int(document.get("version", 0)) > VAULT_VERSION:
        raise VaultFormatError(
            f"vault version {document.get('version')} is newer than this build supports"
        )
    for required in ("kdf", "verifier", "payload"):
        if required not in document:
            raise VaultFormatError(f"vault header is missing '{required}'")
    return document


# --------------------------------------------------------------------------- #
# high-level vault container
# --------------------------------------------------------------------------- #
class VaultFile:
    """Encrypted container holding a list of entry dictionaries."""

    def __init__(
        self,
        path: str = DEFAULT_VAULT_PATH,
        *,
        iterations: int = DEFAULT_ITERATIONS,
        cipher: Optional[str] = None,
    ) -> None:
        self.path = path
        self.iterations = iterations
        self.cipher = cipher or default_cipher()

    # -- creation ---------------------------------------------------------- #
    def create(self, master_password: str, entries: Optional[list] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a brand-new vault, overwriting any existing file."""
        from .models import utcnow

        key, salt = derive_key(master_password, iterations=self.iterations)
        header = {
            "format": VAULT_FORMAT,
            "version": VAULT_VERSION,
            "kdf": {"algo": PBKDF2, "iterations": self.iterations, "salt": b64e(salt)},
            "cipher": self.cipher,
            "verifier": verification_blob(key),
            "meta": {
                "created_at": utcnow(),
                "updated_at": utcnow(),
                "entry_count": len(entries or []),
                "app": "VAULTGUARD",
            },
        }
        payload = {"entries": entries or [], "meta": meta or {}}
        header["payload"] = encrypt(
            key, json.dumps(payload, ensure_ascii=False).encode("utf-8"), cipher=self.cipher, aad=_header_bytes(header)
        )
        _atomic_write(self.path, json.dumps(header, indent=2, sort_keys=False) + "\n")
        return header

    # -- unlocking --------------------------------------------------------- #
    def unlock(self, master_password: str) -> Dict[str, Any]:
        """Verify the master password and return the decrypted payload."""
        header = read_vault_header(self.path)
        kdf = header["kdf"]
        try:
            salt = b64d(kdf["salt"])
            iterations = int(kdf["iterations"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VaultFormatError("vault header has invalid KDF parameters") from exc

        key, _ = derive_key(master_password, salt=salt, iterations=iterations)
        if not check_verification_blob(key, header["verifier"]):
            raise VaultCryptoError("incorrect master password")

        try:
            raw = decrypt(
                key,
                header["payload"],
                aad=_header_bytes(header),
            )
        except VaultCryptoError as exc:
            raise VaultFormatError(
                "vault integrity check failed — the file was modified or corrupted"
            ) from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultFormatError("vault payload is not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise VaultFormatError("vault payload has an unexpected shape")
        self.iterations = iterations
        self.cipher = header.get("cipher", self.cipher)
        return payload

    # -- saving ------------------------------------------------------------ #
    def save(self, master_password: str, entries: list, *, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Re-encrypt and atomically persist ``entries`` with a fresh nonce."""
        from .models import utcnow

        header = read_vault_header(self.path)
        kdf = header["kdf"]
        salt = b64d(kdf["salt"])
        iterations = int(kdf["iterations"])
        if meta is not None:
            header["meta"] = meta
        header.setdefault("meta", {})
        header["meta"]["updated_at"] = utcnow()
        header["meta"]["entry_count"] = len(entries)

        key, _ = derive_key(master_password, salt=salt, iterations=iterations)
        if not check_verification_blob(key, header["verifier"]):
            raise VaultCryptoError("incorrect master password")

        payload = {"entries": entries, "meta": header.get("meta", {}).get("user", {})}
        header["payload"] = encrypt(
            key,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            cipher=header.get("cipher", self.cipher),
            aad=_header_bytes(header),
        )
        _atomic_write(self.path, json.dumps(header, indent=2, sort_keys=False) + "\n")
        return header

    # -- inspection -------------------------------------------------------- #
    def describe(self) -> Dict[str, Any]:
        """Non-secret metadata about the vault on disk."""
        header = read_vault_header(self.path)
        size = os.path.getsize(self.path)
        return {
            "path": self.path,
            "format": header.get("format"),
            "version": header.get("version"),
            "cipher": header.get("cipher"),
            "kdf": header["kdf"].get("algo"),
            "iterations": header["kdf"].get("iterations"),
            "entry_count": header.get("meta", {}).get("entry_count", 0),
            "created_at": header.get("meta", {}).get("created_at"),
            "updated_at": header.get("meta", {}).get("updated_at"),
            "size_bytes": size,
        }

    def change_master_password(self, old_password: str, new_password: str) -> Dict[str, Any]:
        """Re-key the vault with a new master password and a fresh salt."""
        from .models import utcnow

        payload = self.unlock(old_password)
        entries = payload["entries"]
        header = read_vault_header(self.path)
        salt_b64 = header["kdf"]["salt"]

        old_key, _ = derive_key(old_password, salt=b64d(salt_b64), iterations=int(header["kdf"]["iterations"]))
        new_key, new_salt = derive_key(new_password, iterations=int(header["kdf"]["iterations"]))

        header["kdf"] = {
            "algo": PBKDF2,
            "iterations": int(header["kdf"]["iterations"]),
            "salt": b64e(new_salt),
        }
        header["cipher"] = header.get("cipher", HMAC_CTR_ETM)
        header["verifier"] = verification_blob(new_key)
        header.setdefault("meta", {})
        header["meta"]["rotdated_at"] = utcnow()
        header["meta"]["updated_at"] = utcnow()
        header["meta"]["entry_count"] = len(entries)
        header["payload"] = encrypt(
            new_key,
            json.dumps({"entries": entries, "meta": {}}, ensure_ascii=False).encode("utf-8"),
            cipher=header["cipher"],
            aad=_header_bytes(header),
        )
        _atomic_write(self.path, json.dumps(header, indent=2, sort_keys=False) + "\n")
        del old_key
        return self.describe()


def backup_vault(path: str, destination: str) -> str:
    """Copy an encrypted vault file elsewhere (ciphertext only, still locked)."""
    if not vault_exists(path):
        raise VaultFormatError(f"no vault found at {path}")
    os.makedirs(os.path.dirname(os.path.abspath(destination)) or ".", exist_ok=True)
    with open(path, "rb") as src, open(destination, "wb") as dst:
        dst.write(src.read())
    os.chmod(destination, FILE_MODE)
    return destination
