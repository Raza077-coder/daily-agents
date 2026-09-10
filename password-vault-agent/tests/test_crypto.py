"""Cryptography layer tests: KDF, AEAD round-trips, tamper detection."""

from __future__ import annotations

import os

import pytest

from vaultguard.crypto import (
    AES_GCM,
    DEFAULT_ITERATIONS,
    HMAC_CTR_ETM,
    VaultCryptoError,
    VaultKdfError,
    b64d,
    b64e,
    check_verification_blob,
    cipher_available,
    constant_time_eq,
    decrypt,
    default_cipher,
    derive_key,
    encrypt,
    entropy_of_key,
    hash_password_hint,
    verification_blob,
)


class TestBase64:
    def test_round_trip(self):
        raw = os.urandom(64)
        assert b64d(b64e(raw)) == raw

    def test_url_safe_no_padding(self):
        encoded = b64e(b"\xff" * 4)
        assert "=" not in encoded
        assert "+" not in encoded and "/" not in encoded

    def test_decodes_unpadded_input(self):
        raw = os.urandom(33)
        assert b64d(b64e(raw)) == raw


class TestKeyDerivation:
    def test_deterministic_for_same_inputs(self):
        salt = os.urandom(16)
        first, _ = derive_key("hunter2", salt=salt)
        second, _ = derive_key("hunter2", salt=salt)
        assert first == second

    def test_different_password_gives_different_key(self):
        salt = os.urandom(16)
        first, _ = derive_key("alpha", salt=salt)
        second, _ = derive_key("beta", salt=salt)
        assert first != second

    def test_different_salt_gives_different_key(self):
        first, _ = derive_key("same", salt=os.urandom(16))
        second, _ = derive_key("same", salt=os.urandom(16))
        assert first != second

    def test_generates_salt_when_absent(self):
        key, salt = derive_key("password")
        assert len(salt) == 16
        assert len(key) == 32

    def test_rejects_empty_password(self):
        with pytest.raises(VaultKdfError):
            derive_key("")

    def test_rejects_low_iterations(self):
        with pytest.raises(VaultKdfError):
            derive_key("password", iterations=10)

    def test_rejects_short_salt(self):
        with pytest.raises(VaultKdfError):
            derive_key("password", salt=b"tiny")

    def test_default_iterations_meet_owasp_floor(self):
        assert DEFAULT_ITERATIONS >= 200_000


class TestAead:
    @pytest.mark.parametrize("cipher", [HMAC_CTR_ETM, AES_GCM])
    def test_round_trip(self, cipher):
        if not cipher_available(cipher):
            pytest.skip(f"{cipher} unavailable on this interpreter")
        key, _ = derive_key("password", os.urandom(16))
        blob = encrypt(key, b"top secret payload", cipher=cipher, aad=b"meta")
        assert decrypt(key, blob, aad=b"meta") == b"top secret payload"

    def test_round_trip_with_both_ciphers(self):
        key, _ = derive_key("password", os.urandom(16))
        for cipher in (HMAC_CTR_ETM, default_cipher()):
            blob = encrypt(key, b"hello", cipher=cipher, aad=b"x")
            assert decrypt(key, blob, aad=b"x") == b"hello"

    def test_empty_plaintext(self):
        key, _ = derive_key("password", os.urandom(16))
        blob = encrypt(key, b"", aad=b"")
        assert decrypt(key, blob) == b""

    def test_large_plaintext_spanning_many_blocks(self):
        key, _ = derive_key("password", os.urandom(16))
        payload = os.urandom(10_000)
        blob = encrypt(key, payload, cipher=HMAC_CTR_ETM)
        assert decrypt(key, blob) == payload

    def test_wrong_key_fails(self):
        key_a, _ = derive_key("alpha", os.urandom(16))
        key_b, _ = derive_key("beta", os.urandom(16))
        blob = encrypt(key_a, b"secret", cipher=HMAC_CTR_ETM)
        with pytest.raises(VaultCryptoError):
            decrypt(key_b, blob)

    def test_tampered_ciphertext_fails(self):
        key, _ = derive_key("password", os.urandom(16))
        blob = encrypt(key, b"secret", cipher=HMAC_CTR_ETM)
        raw = bytearray(b64d(blob["ct"]))
        raw[0] ^= 0x01
        blob["ct"] = b64e(bytes(raw))
        with pytest.raises(VaultCryptoError):
            decrypt(key, blob)

    def test_tampered_nonce_fails(self):
        key, _ = derive_key("password", os.urandom(16))
        blob = encrypt(key, b"secret", cipher=HMAC_CTR_ETM)
        raw = bytearray(b64d(blob["nonce"]))
        raw[0] ^= 0x01
        blob["nonce"] = b64e(bytes(raw))
        with pytest.raises(VaultCryptoError):
            decrypt(key, blob)

    def test_tampered_tag_fails(self):
        key, _ = derive_key("password", os.urandom(16))
        blob = encrypt(key, b"secret", cipher=HMAC_CTR_ETM)
        raw = bytearray(b64d(blob["tag"]))
        raw[0] ^= 0x01
        blob["tag"] = b64e(bytes(raw))
        with pytest.raises(VaultCryptoError):
            decrypt(key, blob)

    def test_aad_mismatch_fails(self):
        key, _ = derive_key("password", os.urandom(16))
        blob = encrypt(key, b"secret", cipher=HMAC_CTR_ETM, aad=b"right")
        with pytest.raises(VaultCryptoError):
            decrypt(key, blob, aad=b"wrong")

    def test_nonce_is_fresh_per_call(self):
        key, _ = derive_key("password", os.urandom(16))
        first = encrypt(key, b"same", cipher=HMAC_CTR_ETM)
        second = encrypt(key, b"same", cipher=HMAC_CTR_ETM)
        assert first["nonce"] != second["nonce"]
        assert first["ct"] != second["ct"]

    def test_rejects_bad_key_length(self):
        with pytest.raises(VaultCryptoError):
            encrypt(b"short", b"data")

    def test_rejects_malformed_blob(self):
        key, _ = derive_key("password", os.urandom(16))
        with pytest.raises(VaultCryptoError):
            decrypt(key, {"cipher": HMAC_CTR_ETM})
        with pytest.raises(VaultCryptoError):
            decrypt(key, "not-a-dict")  # type: ignore[arg-type]

    def test_rejects_unknown_cipher(self):
        key, _ = derive_key("password", os.urandom(16))
        with pytest.raises(VaultCryptoError):
            decrypt(key, {"cipher": "rot13", "nonce": b64e(b"x" * 16), "ct": b64e(b"y")})


class TestVerifier:
    def test_accepts_correct_key(self):
        key, _ = derive_key("password", os.urandom(16))
        assert check_verification_blob(key, verification_blob(key))

    def test_rejects_wrong_key(self):
        key_a, _ = derive_key("alpha", os.urandom(16))
        key_b, _ = derive_key("beta", os.urandom(16))
        assert not check_verification_blob(key_b, verification_blob(key_a))


class TestHelpers:
    def test_constant_time_compare(self):
        assert constant_time_eq("abc", "abc")
        assert not constant_time_eq("abc", "abd")

    def test_hint_is_deterministic_and_not_the_password(self):
        first = hash_password_hint("secret")
        second = hash_password_hint("secret")
        assert first == second
        assert "secret" not in first

    def test_hint_differs_across_passwords(self):
        assert hash_password_hint("alpha") != hash_password_hint("beta")

    def test_entropy_of_random_key_is_high(self):
        assert 3.0 < entropy_of_key(os.urandom(512)) <= 8.0

    def test_entropy_of_constant_key_is_zero(self):
        assert entropy_of_key(b"\x00" * 64) == 0.0

    def test_default_cipher_is_available(self):
        assert cipher_available(default_cipher())
