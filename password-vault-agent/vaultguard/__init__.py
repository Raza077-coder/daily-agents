"""VAULTGUARD — a secure, offline, deterministic password vault.

Zero network calls, zero third-party dependencies in the core engine.
Everything is derived from a single master password.
"""

from __future__ import annotations

from .audit import VaultAuditor
from .crypto import (
    AES_GCM,
    DEFAULT_ITERATIONS,
    HMAC_CTR_ETM,
    VaultCryptoError,
    decrypt,
    default_cipher,
    derive_key,
    encrypt,
    hash_password_hint,
)
from .engine import VaultAuthError, VaultEngine, VaultError
from .generator import (
    DEFAULT_WORDLIST,
    generate_passphrase,
    generate_password,
    generate_pin,
)
from .models import VaultEntry
from .strength import StrengthReport, estimate_strength
from .storage import (
    VAULT_FORMAT,
    VaultFormatError,
    read_vault_header,
    vault_exists,
)

__all__ = [
    "AES_GCM",
    "DEFAULT_ITERATIONS",
    "DEFAULT_WORDLIST",
    "HMAC_CTR_ETM",
    "StrengthReport",
    "VAULT_FORMAT",
    "VaultAuditor",
    "VaultAuthError",
    "VaultCryptoError",
    "VaultEngine",
    "VaultEntry",
    "VaultError",
    "VaultFormatError",
    "decrypt",
    "default_cipher",
    "derive_key",
    "encrypt",
    "estimate_strength",
    "generate_passphrase",
    "generate_password",
    "generate_pin",
    "hash_password_hint",
    "read_vault_header",
    "vault_exists",
]

__version__ = "1.0.0"
