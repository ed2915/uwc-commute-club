#!/usr/bin/env python3
"""Create and manage encrypted backups of Render submissions."""

from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timedelta
import getpass
import hashlib
import io
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import sys
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from render_submissions import (
    AdminClient,
    AdminError,
    DEFAULT_BASE_URL,
    FIELDS,
    expire_unanswered_consent,
)


SAST = ZoneInfo("Africa/Johannesburg")
MAGIC = b"UWCCBKP1"
NONCE_SIZE = 12
KEY_SIZE = 32
KEYCHAIN_SERVICE = "za.ac.uwc.commute-club.backup-key"
LAUNCH_AGENT_LABEL = "za.ac.uwc.commute-club.backup"
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TOKEN_FILE = Path.home() / ".config/uwc-commute-club/admin-token"
DEFAULT_BACKUP_DIR = PROJECT_DIR / "backups"
DEFAULT_LOG_PATH = PROJECT_DIR / "logs/backup.log"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_BACKUP_HOUR = 20
LEGACY_FIELDS_WITHOUT_REMINDER_COUNT = [
    field for field in FIELDS if field != "consent_reminder_count"
]


class BackupError(RuntimeError):
    """Raised when a backup operation cannot be completed safely."""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create and manage encrypted UWC Commute Club CSV backups."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("UWC_ADMIN_BASE_URL", DEFAULT_BASE_URL),
        help=f"Render service URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("UWC_ADMIN_TOKEN"),
        help="Admin token. By default it is read from the existing protected token file.",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=DEFAULT_TOKEN_FILE,
        help=f"Admin-token file. Default: {DEFAULT_TOKEN_FILE}",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help=f"Encrypted backup directory. Default: {DEFAULT_BACKUP_DIR}",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification if this Python install requires it.",
    )

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-key", help="Create the dedicated encryption key in macOS Keychain.")

    backup_parser = commands.add_parser("backup", help="Create today's encrypted CSV backup.")
    backup_parser.add_argument(
        "--force",
        action="store_true",
        help="Create another backup even if one already exists for today.",
    )

    maintenance_parser = commands.add_parser(
        "daily-maintenance",
        help="Create today's encrypted backup, then delete expired unanswered consent rows.",
    )
    maintenance_parser.add_argument(
        "--retention-days",
        type=positive_integer,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Delete encrypted backups older than this many days. Default: {DEFAULT_RETENTION_DAYS}.",
    )
    backup_parser.add_argument(
        "--retention-days",
        type=positive_integer,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Delete encrypted backups older than this many days. Default: {DEFAULT_RETENTION_DAYS}.",
    )

    commands.add_parser("list", help="List encrypted backups without decrypting their contents.")

    verify_parser = commands.add_parser("verify", help="Verify backup encryption and CSV structure.")
    verify_parser.add_argument(
        "paths",
        type=Path,
        nargs="*",
        help="Backups to verify. If omitted, verify every backup in the backup directory.",
    )

    decrypt_parser = commands.add_parser(
        "decrypt",
        help="Decrypt one backup to a protected CSV file for recovery.",
    )
    decrypt_parser.add_argument("path", type=Path, help="Encrypted backup to decrypt.")
    decrypt_parser.add_argument("--output", type=Path, required=True, help="Plaintext CSV output path.")
    decrypt_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the output file if it already exists.",
    )

    schedule_parser = commands.add_parser(
        "install-schedule",
        help="Install or update the daily macOS LaunchAgent.",
    )
    schedule_parser.add_argument(
        "--hour",
        type=hour_value,
        default=DEFAULT_BACKUP_HOUR,
        help=f"Local hour for the daily backup. Default: {DEFAULT_BACKUP_HOUR}:00.",
    )

    args = parser.parse_args()

    try:
        if args.command == "init-key":
            initialize_key()
        elif args.command == "backup":
            token = load_admin_token(args.token, args.token_file)
            create_backup(
                AdminClient(args.base_url, token, insecure=args.insecure),
                args.backup_dir,
                args.retention_days,
                force=args.force,
            )
        elif args.command == "daily-maintenance":
            token = load_admin_token(args.token, args.token_file)
            client = AdminClient(args.base_url, token, insecure=args.insecure)
            print(f"Daily maintenance started: {datetime.now(SAST):%Y-%m-%d %H:%M:%S SAST}")
            create_backup(
                client,
                args.backup_dir,
                args.retention_days,
                force=False,
            )
            expire_unanswered_consent(client, client.list_submissions(), apply=True)
            print(f"Daily maintenance completed: {datetime.now(SAST):%Y-%m-%d %H:%M:%S SAST}")
        elif args.command == "list":
            list_backups(args.backup_dir)
        elif args.command == "verify":
            verify_backups(args.paths or encrypted_backup_paths(args.backup_dir))
        elif args.command == "decrypt":
            decrypt_backup(args.path, args.output, force=args.force)
        elif args.command == "install-schedule":
            install_schedule(args, args.hour)
    except (AdminError, BackupError, OSError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Use a positive integer.")
    return number


def hour_value(value: str) -> int:
    hour = int(value)
    if not 0 <= hour <= 23:
        raise argparse.ArgumentTypeError("Use an hour from 0 to 23.")
    return hour


def load_admin_token(explicit_token: str | None, token_file: Path) -> str:
    if explicit_token:
        return explicit_token.strip()
    try:
        token = token_file.expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise BackupError(
            f"Admin token not found at {token_file}. Set UWC_ADMIN_TOKEN or use --token-file."
        ) from error
    if not token:
        raise BackupError(f"Admin token file is empty: {token_file}")
    return token


def keychain_account() -> str:
    return getpass.getuser()


def initialize_key() -> None:
    if key_exists():
        print(f"Encryption key already exists in Keychain for {KEYCHAIN_SERVICE}.")
        return

    encoded_key = base64.urlsafe_b64encode(secrets.token_bytes(KEY_SIZE)).decode("ascii")
    result = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-a",
            keychain_account(),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            encoded_key,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BackupError(result.stderr.strip() or "Could not store the backup key in Keychain.")
    print(f"Created dedicated encryption key in Keychain for {KEYCHAIN_SERVICE}.")


def key_exists() -> bool:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            keychain_account(),
            "-s",
            KEYCHAIN_SERVICE,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def load_encryption_key() -> bytes:
    environment_key = os.environ.get("UWC_BACKUP_KEY")
    if environment_key:
        encoded_key = environment_key.strip()
    else:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                keychain_account(),
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BackupError(
                "Backup encryption key is missing. Run: "
                "python3 tools/backup_render_submissions.py init-key"
            )
        encoded_key = result.stdout.strip()

    try:
        key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise BackupError("The backup encryption key is not valid base64.") from error
    if len(key) != KEY_SIZE:
        raise BackupError("The backup encryption key must decode to exactly 32 bytes.")
    return key


def submissions_csv(submissions: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(submissions)
    return buffer.getvalue().encode("utf-8")


def encrypt_bytes(plaintext: bytes, key: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    return MAGIC + nonce + ciphertext


def decrypt_bytes(payload: bytes, key: bytes) -> bytes:
    minimum_size = len(MAGIC) + NONCE_SIZE + 16
    if len(payload) < minimum_size or not payload.startswith(MAGIC):
        raise BackupError("This is not a recognized UWC Commute Club backup.")
    nonce_start = len(MAGIC)
    nonce = payload[nonce_start : nonce_start + NONCE_SIZE]
    ciphertext = payload[nonce_start + NONCE_SIZE :]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, MAGIC)
    except InvalidTag as error:
        raise BackupError("Backup authentication failed: the file or key is not valid.") from error


def create_backup(
    client: AdminClient,
    backup_dir: Path,
    retention_days: int,
    *,
    force: bool,
) -> Path | None:
    now = datetime.now(SAST)
    backup_dir = prepare_backup_directory(backup_dir)
    todays_backups = list(backup_dir.glob(f"submissions-{now:%Y%m%d}T*.csv.aes"))
    if todays_backups and not force:
        print(f"Today's encrypted backup already exists: {todays_backups[-1]}")
        prune_old_backups(backup_dir, retention_days, now)
        return None

    submissions = client.list_submissions()
    plaintext = submissions_csv(submissions)
    key = load_encryption_key()
    encrypted = encrypt_bytes(plaintext, key)

    if decrypt_bytes(encrypted, key) != plaintext:
        raise BackupError("Encrypted backup failed its in-memory round-trip check.")

    destination = backup_dir / f"submissions-{now:%Y%m%dT%H%M%S}-SAST.csv.aes"
    write_private_atomic(destination, encrypted)
    row_count = validate_csv(plaintext)
    checksum = hashlib.sha256(encrypted).hexdigest()[:16]
    print(f"Created encrypted backup: {destination}")
    print(f"Rows backed up: {row_count}")
    print(f"Encrypted size: {destination.stat().st_size} bytes")
    print(f"SHA-256 prefix: {checksum}")
    prune_old_backups(backup_dir, retention_days, now)
    return destination


def prepare_backup_directory(path: Path) -> Path:
    directory = path.expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory


def write_private_atomic(destination: Path, payload: bytes) -> None:
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(6)}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def encrypted_backup_paths(backup_dir: Path) -> list[Path]:
    directory = backup_dir.expanduser()
    if not directory.exists():
        return []
    return sorted(directory.glob("submissions-*.csv.aes"))


def list_backups(backup_dir: Path) -> None:
    paths = encrypted_backup_paths(backup_dir)
    if not paths:
        print(f"No encrypted backups found in {backup_dir.expanduser()}.")
        return
    for path in paths:
        modified = datetime.fromtimestamp(path.stat().st_mtime, SAST)
        print(f"{path.name}  {path.stat().st_size} bytes  {modified:%Y-%m-%d %H:%M:%S SAST}")


def verify_backups(paths: list[Path]) -> None:
    if not paths:
        raise BackupError("No encrypted backups were found to verify.")
    key = load_encryption_key()
    for path in paths:
        plaintext = decrypt_bytes(path.expanduser().read_bytes(), key)
        row_count = validate_csv(plaintext)
        print(f"Verified {path.expanduser()}: {row_count} rows")


def validate_csv(plaintext: bytes) -> int:
    try:
        text = plaintext.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames not in [FIELDS, LEGACY_FIELDS_WITHOUT_REMINDER_COUNT]:
            raise BackupError("Backup CSV columns do not match the expected submission schema.")
        return sum(1 for _ in reader)
    except UnicodeDecodeError as error:
        raise BackupError("Backup plaintext is not valid UTF-8 CSV.") from error


def decrypt_backup(path: Path, output: Path, *, force: bool) -> None:
    source = path.expanduser()
    destination = output.expanduser()
    if destination.exists() and not force:
        raise BackupError(f"Output already exists: {destination}. Use --force to replace it.")
    plaintext = decrypt_bytes(source.read_bytes(), load_encryption_key())
    row_count = validate_csv(plaintext)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_private_atomic(destination, plaintext)
    print(f"Decrypted {row_count} rows to protected file: {destination}")
    print("Delete the plaintext recovery file securely when it is no longer needed.")


def prune_old_backups(backup_dir: Path, retention_days: int, now: datetime) -> None:
    cutoff = now - timedelta(days=retention_days)
    removed = []
    for path in encrypted_backup_paths(backup_dir):
        modified = datetime.fromtimestamp(path.stat().st_mtime, SAST)
        if modified < cutoff:
            path.unlink()
            removed.append(path.name)
    if removed:
        print(f"Removed {len(removed)} encrypted backup(s) older than {retention_days} days.")


def install_schedule(args: argparse.Namespace, hour: int) -> None:
    if not key_exists():
        raise BackupError("Initialize the encryption key before installing the schedule.")

    launch_agents = Path.home() / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    DEFAULT_LOG_PATH.parent.chmod(0o700)
    plist_path = launch_agents / f"{LAUNCH_AGENT_LABEL}.plist"
    log_path = DEFAULT_LOG_PATH
    script_path = Path(__file__).resolve()

    arguments = [
        sys.executable,
        str(script_path),
        "--base-url",
        args.base_url,
        "--token-file",
        str(args.token_file.expanduser()),
        "--backup-dir",
        str(args.backup_dir.expanduser()),
    ]
    if args.insecure:
        arguments.append("--insecure")
    arguments.append("daily-maintenance")

    configuration = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": arguments,
        "WorkingDirectory": str(PROJECT_DIR),
        "RunAtLoad": True,
        "StartCalendarInterval": {"Hour": hour, "Minute": 0},
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    payload = plistlib.dumps(configuration, fmt=plistlib.FMT_XML, sort_keys=True)
    write_private_atomic(plist_path, payload)

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["/bin/launchctl", "bootout", domain, str(plist_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(plist_path)], check=True)
    subprocess.run(["/bin/launchctl", "enable", f"{domain}/{LAUNCH_AGENT_LABEL}"], check=True)

    print(f"Installed daily maintenance schedule: {hour:02d}:00 local time")
    print("The job backs up first, then removes expired unanswered consent rows.")
    print("It also runs at login and skips creating a duplicate backup on the same day.")
    print(f"LaunchAgent: {plist_path}")
    print(f"Log: {log_path}")


if __name__ == "__main__":
    raise SystemExit(main())
