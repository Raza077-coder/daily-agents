"""Storage layer tests: vault file format, atomic save, tamper detection."""

from __future__ import annotations

import json
import os

import pytest

from vaultguard.crypto import VaultCryptoError
from vaultguard.storage import (
    VAULT_FORMAT,
    VAULT_VERSION,
    VaultFile,
    VaultFormatError,
    backup_vault,
    read_vault_header,
    vault_exists,
)

MASTER = "storage-test-master"
ITERATIONS = 10_000


@pytest.fixture()
def vault_file(vault_path):
    return VaultFile(vault_path, iterations=ITERATIONS)


class TestVaultFileLifecycle:
    def test_create_writes_a_readable_header(self, vault_file, vault_path):
        vault_file.create(MASTER, entries=[])
        assert vault_exists(vault_path)
        header = read_vault_header(vault_path)
        assert header["format"] == VAULT_FORMAT
        assert header["version"] == VAULT_VERSION
        assert header["kdf"]["algo"] == "pbkdf2-hmac-sha256"
        assert header["kdf"]["iterations"] == ITERATIONS

    def test_created_file_is_owner_only(self, vault_file, vault_path):
        vault_file.create(MASTER)
        mode = os.stat(vault_path).st_mode & 0o777
        assert mode == 0o600

    def test_payload_is_not_plaintext_on_disk(self, vault_file, vault_path):
        vault_file.create(MASTER, entries=[{"title": "GitHub", "password": "SuperSecret123!"}])
        with open(vault_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        assert "SuperSecret123!" not in raw
        assert "GitHub" not in raw

    def test_unlock_returns_payload(self, vault_file):
        vault_file.create(MASTER, entries=[{"title": "A", "password": "p"}])
        payload = vault_file.unlock(MASTER)
        assert payload["entries"][0]["title"] == "A"

    def test_wrong_password_raises(self, vault_file):
        vault_file.create(MASTER)
        with pytest.raises(VaultCryptoError):
            vault_file.unlock("not-the-password")

    def test_save_then_unlock_round_trip(self, vault_file):
        vault_file.create(MASTER, entries=[])
        vault_file.save(MASTER, [{"title": "Saved", "password": "abc"}])
        payload = vault_file.unlock(MASTER)
        assert payload["entries"][0]["title"] == "Saved"

    def test_save_uses_a_fresh_nonce(self, vault_file, vault_path):
        vault_file.create(MASTER, entries=[])
        vault_file.save(MASTER, [{"title": "X", "password": "p"}])
        first = read_vault_header(vault_path)["payload"]["nonce"]
        vault_file.save(MASTER, [{"title": "X", "password": "p"}])
        second = read_vault_header(vault_path)["payload"]["nonce"]
        assert first != second

    def test_metadata_tracks_entry_count(self, vault_file):
        vault_file.create(MASTER, entries=[])
        vault_file.save(MASTER, [{"title": "1"}, {"title": "2"}])
        assert vault_file.describe()["entry_count"] == 2

    def test_describe_reports_no_secrets(self, vault_file):
        vault_file.create(MASTER)
        described = vault_file.describe()
        assert "payload" not in described
        assert "verifier" not in described
        assert described["size_bytes"] > 0

    def test_change_master_password_rotates_salt(self, vault_file, vault_path):
        vault_file.create(MASTER, entries=[{"title": "A", "password": "p"}])
        old_salt = read_vault_header(vault_path)["kdf"]["salt"]
        vault_file.change_master_password(MASTER, "brand-new-master")
        new_salt = read_vault_header(vault_path)["kdf"]["salt"]
        assert old_salt != new_salt
        # old password no longer opens it
        with pytest.raises(VaultCryptoError):
            vault_file.unlock(MASTER)
        assert vault_file.unlock("brand-new-master")["entries"][0]["title"] == "A"

    def test_write_is_atomic_leaving_no_temp_files(self, vault_file, vault_path):
        vault_file.create(MASTER)
        directory = os.path.dirname(vault_path)
        leftovers = [n for n in os.listdir(directory) if n.startswith(".vaultguard-")]
        assert leftovers == []


class TestTamperDetection:
    def test_modified_ciphertext_is_rejected(self, vault_file, vault_path):
        vault_file.create(MASTER, entries=[{"title": "A"}])
        with open(vault_path, encoding="utf-8") as handle:
            document = json.load(handle)
        ct = list(document["payload"]["ct"])
        ct[0] = "A" if ct[0] != "A" else "B"
        document["payload"]["ct"] = "".join(ct)
        with open(vault_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with pytest.raises(VaultFormatError):
            vault_file.unlock(MASTER)

    def test_modified_iterations_are_rejected(self, vault_file, vault_path):
        """The header is authenticated, so editing the KDF params must fail."""
        vault_file.create(MASTER, entries=[{"title": "A"}])
        with open(vault_path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["kdf"]["iterations"] = ITERATIONS + 1
        with open(vault_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with pytest.raises((VaultFormatError, VaultCryptoError)):
            vault_file.unlock(MASTER)

    def test_modified_salt_is_rejected(self, vault_file, vault_path):
        vault_file.create(MASTER, entries=[{"title": "A"}])
        with open(vault_path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["kdf"]["salt"] = "AAAAAAAAAAAAAAAAAAAAAA"
        with open(vault_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with pytest.raises((VaultFormatError, VaultCryptoError)):
            vault_file.unlock(MASTER)


class TestHeaderValidation:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(VaultFormatError):
            read_vault_header(str(tmp_path / "nope.json"))

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(VaultFormatError):
            read_vault_header(str(path))

    def test_wrong_format_marker_raises(self, tmp_path):
        path = tmp_path / "other.json"
        path.write_text(json.dumps({"format": "not-vaultguard"}), encoding="utf-8")
        with pytest.raises(VaultFormatError):
            read_vault_header(str(path))

    def test_newer_version_raises(self, tmp_path):
        path = tmp_path / "future.json"
        path.write_text(
            json.dumps({"format": VAULT_FORMAT, "version": 99, "kdf": {}, "verifier": {}, "payload": {}}),
            encoding="utf-8",
        )
        with pytest.raises(VaultFormatError):
            read_vault_header(str(path))

    def test_missing_required_section_raises(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"format": VAULT_FORMAT, "version": 1}), encoding="utf-8")
        with pytest.raises(VaultFormatError):
            read_vault_header(str(path))

    def test_vault_exists_reports_correctly(self, vault_path):
        assert vault_exists(vault_path) is False
        VaultFile(vault_path, iterations=ITERATIONS).create(MASTER)
        assert vault_exists(vault_path) is True


class TestBackup:
    def test_backup_copies_ciphertext_verbatim(self, vault_file, vault_path, tmp_path):
        vault_file.create(MASTER, entries=[{"title": "A", "password": "p"}])
        destination = str(tmp_path / "backup.json")
        backup_vault(vault_path, destination)
        with open(vault_path, "rb") as a, open(destination, "rb") as b:
            assert a.read() == b.read()

    def test_backup_requires_an_existing_vault(self, vault_path, tmp_path):
        with pytest.raises(VaultFormatError):
            backup_vault(vault_path, str(tmp_path / "out.json"))

    def test_restored_backup_opens_with_the_same_password(self, vault_file, vault_path, tmp_path):
        vault_file.create(MASTER, entries=[{"title": "Kept", "password": "p"}])
        destination = str(tmp_path / "backup.json")
        backup_vault(vault_path, destination)
        assert VaultFile(destination).unlock(MASTER)["entries"][0]["title"] == "Kept"
