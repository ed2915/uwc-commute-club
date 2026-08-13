#!/usr/bin/env python3
"""Tests for the encrypted Render backup utility."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest

from backup_render_submissions import (
    BackupError,
    FIELDS,
    LEGACY_FIELDS_WITHOUT_REMINDER_COUNT,
    SAST,
    decrypt_bytes,
    encrypt_bytes,
    encrypted_backup_paths,
    prune_old_backups,
    submissions_csv,
    validate_csv,
    write_private_atomic,
)


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = bytes(range(32))
        self.rows = [{field: f"value-{field}" for field in FIELDS}]

    def test_encrypted_csv_round_trip(self) -> None:
        plaintext = submissions_csv(self.rows)
        encrypted = encrypt_bytes(plaintext, self.key)

        self.assertNotIn(b"value-student_number", encrypted)
        self.assertEqual(decrypt_bytes(encrypted, self.key), plaintext)
        self.assertEqual(validate_csv(plaintext), 1)

    def test_tampering_is_detected(self) -> None:
        encrypted = bytearray(encrypt_bytes(submissions_csv(self.rows), self.key))
        encrypted[-1] ^= 1

        with self.assertRaises(BackupError):
            decrypt_bytes(bytes(encrypted), self.key)

    def test_previous_schema_without_reminder_count_remains_recoverable(self) -> None:
        row = {field: f"value-{field}" for field in LEGACY_FIELDS_WITHOUT_REMINDER_COUNT}
        buffer = submissions_csv([row]).decode("utf-8")
        current_header, data = buffer.splitlines()
        legacy_header = ",".join(LEGACY_FIELDS_WITHOUT_REMINDER_COUNT)
        current_values = data.split(",")
        reminder_index = FIELDS.index("consent_reminder_count")
        legacy_values = current_values[:reminder_index] + current_values[reminder_index + 1 :]
        legacy_csv = f"{legacy_header}\n{','.join(legacy_values)}\n".encode("utf-8")

        self.assertEqual(validate_csv(legacy_csv), 1)

    def test_private_atomic_write_uses_restrictive_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "backup.csv.aes"
            write_private_atomic(destination, b"encrypted")

            self.assertEqual(destination.read_bytes(), b"encrypted")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_pruning_removes_only_expired_backups(self) -> None:
        now = datetime.now(SAST)
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = Path(directory)
            old = backup_dir / "submissions-20260101T000000-SAST.csv.aes"
            recent = backup_dir / "submissions-20260813T000000-SAST.csv.aes"
            old.write_bytes(b"old")
            recent.write_bytes(b"recent")
            old_time = (now - timedelta(days=31)).timestamp()
            os.utime(old, (old_time, old_time))

            prune_old_backups(backup_dir, 30, now)

            self.assertEqual(encrypted_backup_paths(backup_dir), [recent])


if __name__ == "__main__":
    unittest.main()
