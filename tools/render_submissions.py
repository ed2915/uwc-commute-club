#!/usr/bin/env python3
"""Inspect and maintain UWC Commute Club submissions on Render."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_BASE_URL = "https://uwc-commute-club.onrender.com"
CONSENT_EMAIL_FILE = Path(__file__).resolve().parent.parent / "consent_email.txt"
REMOVAL_EMAIL_FILE = Path(__file__).resolve().parent.parent / "removal_email.txt"
SAST = ZoneInfo("Africa/Johannesburg")
CONSENT_REMINDER_INTERVAL = timedelta(days=3)
MAX_CONSENT_REMINDERS = 3
FIELDS = [
    "id",
    "submitted_at",
    "direction",
    "area",
    "schedule",
    "student_number",
    "email_address",
    "email_sharing_consent",
    "status",
    "connection_requests",
    "connected_student_numbers",
    "consent_token",
    "consent_email_sent_at",
    "consent_email_last_sent_at",
    "consent_reminder_count",
    "consent_response",
    "consent_responded_at",
]

REQUEST_FIELDS = [
    "id",
    "requested_at",
    "student_number",
    "direction",
    "area",
    "schedule",
    "requested_member_labels",
    "requested_submission_ids",
    "status",
    "notes",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show, delete, and edit UWC Commute Club Render submissions."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("UWC_ADMIN_BASE_URL", DEFAULT_BASE_URL),
        help=f"Render service URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("UWC_ADMIN_TOKEN"),
        help="Admin token. Prefer setting UWC_ADMIN_TOKEN instead of passing this.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification if this Python install lacks macOS certificates.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List all submissions.")
    subparsers.add_parser("requests", help="List connection requests.")
    subparsers.add_parser("json", help="Print raw JSON.")
    dedupe_parser = subparsers.add_parser(
        "dedupe",
        help="Remove duplicate pool interests from existing rows.",
    )
    dedupe_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the dedupe plan. Without this, only prints what would change.",
    )

    delete_parser = subparsers.add_parser("delete", help="Delete a submission by id.")
    delete_parser.add_argument("id")

    delete_request_parser = subparsers.add_parser(
        "delete-request",
        help="Delete a historical connection-request record by id.",
    )
    delete_request_parser.add_argument("id")

    patch_parser = subparsers.add_parser("set", help="Update fields for a submission.")
    patch_parser.add_argument("id")
    patch_parser.add_argument("--direction", choices=["to_uwc", "from_uwc"])
    patch_parser.add_argument("--area")
    patch_parser.add_argument("--schedule", help="Example: mon@07:00|wed@08:30")
    patch_parser.add_argument("--student-number")
    patch_parser.add_argument("--email-address")
    patch_parser.add_argument("--email-sharing-consent", choices=["", "yes"])
    patch_parser.add_argument(
        "--status",
        choices=["0", "1", "2", "pending", "matched", "deleted", "archived"],
    )
    patch_parser.add_argument(
        "--connection-requests",
        help="Pipe-separated 6- or 7-digit UWC numbers this row has requested to connect with.",
    )
    patch_parser.add_argument(
        "--connected-student-numbers",
        help="Pipe-separated 6- or 7-digit UWC numbers already connected with this row.",
    )
    patch_parser.add_argument("--consent-token")
    patch_parser.add_argument("--consent-email-sent-at")
    patch_parser.add_argument("--consent-email-last-sent-at")
    patch_parser.add_argument("--consent-reminder-count", choices=["0", "1", "2", "3"])
    patch_parser.add_argument("--consent-response", choices=["", "yes", "no"])
    patch_parser.add_argument("--consent-responded-at")

    connect_parser = subparsers.add_parser(
        "connect",
        help="Manually update connected student numbers for one submission.",
    )
    connect_parser.add_argument("id", help="Submission id to update.")
    connect_group = connect_parser.add_mutually_exclusive_group(required=True)
    connect_group.add_argument(
        "--add",
        nargs="+",
        metavar="SN",
        help="Add one or more 6- or 7-digit UWC numbers to connected_student_numbers.",
    )
    connect_group.add_argument(
        "--remove",
        nargs="+",
        metavar="SN",
        help="Remove one or more 6- or 7-digit UWC numbers from connected_student_numbers.",
    )
    connect_group.add_argument(
        "--set",
        nargs="*",
        metavar="SN",
        help="Replace connected_student_numbers. Use --set with no SNs to clear it.",
    )

    cleanup_parser = subparsers.add_parser(
        "delete-without-student-number",
        help="Delete rows that do not have a usable student or staff identity.",
    )
    cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete rows. Without this, only prints what would be deleted.",
    )

    normalize_status_parser = subparsers.add_parser(
        "status-to-zero",
        help="Change legacy blank/pending submission statuses to 0.",
    )
    normalize_status_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update rows. Without this, only prints what would change.",
    )

    consent_parser = subparsers.add_parser(
        "consent-email",
        help="Generate email text with yes/no consent links for a connection request row.",
    )
    consent_parser.add_argument("id", help="Submission id with status 1 and connection_requests.")
    consent_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate an email that was already marked as sent.",
    )

    target_parser = subparsers.add_parser(
        "target-emails",
        help="Generate one email per target after requester consent is yes.",
    )
    target_parser.add_argument("id", help="Submission id with consent_response yes.")
    target_parser.add_argument(
        "--apply",
        action="store_true",
        help="After you have sent the emails, move pending SNs to connected_student_numbers and set status 2.",
    )

    suggest_parser = subparsers.add_parser(
        "suggest-matches",
        help="Suggest active pools with the same direction, area, and overlapping schedule.",
    )
    suggest_parser.add_argument(
        "--min-size",
        type=int,
        default=2,
        help="Minimum number of submissions in a suggested pool. Default: 2",
    )
    suggest_parser.add_argument(
        "--apply",
        action="store_true",
        help="Mark suggested rows as matched.",
    )

    subparsers.add_parser(
        "review-actions",
        help="Interactively review consent and target emails that need organiser approval.",
    )

    args = parser.parse_args()

    if not args.token:
        print("Set UWC_ADMIN_TOKEN or pass --token.", file=sys.stderr)
        return 2

    client = AdminClient(args.base_url, args.token, insecure=args.insecure)

    try:
        if args.command == "list":
            submissions = client.list_submissions()
            print_table(submissions)
        elif args.command == "requests":
            requests = client.list_connection_requests()
            print_table(requests, fields=REQUEST_FIELDS)
        elif args.command == "json":
            print(json.dumps(client.list_submissions(), indent=2))
        elif args.command == "delete":
            result = client.delete_submission(args.id)
            print(f"Deleted {result.get('deleted', args.id)}")
        elif args.command == "delete-request":
            result = client.delete_connection_request(args.id)
            print(f"Deleted request {result.get('deleted', args.id)}")
        elif args.command == "dedupe":
            submissions = client.list_submissions()
            plan = dedupe_plan(submissions)
            print_dedupe_plan(plan)
            if args.apply:
                apply_dedupe_plan(client, plan)
        elif args.command == "set":
            patch = build_patch(args)
            if not patch:
                print("Provide at least one field to update.", file=sys.stderr)
                return 2
            result = client.patch_submission(args.id, patch)
            print("Updated:")
            print_table([result["submission"]])
        elif args.command == "connect":
            submissions = client.list_submissions()
            result = update_connected_students(client, submissions, args)
            print("Updated:")
            print_table([result["submission"]])
        elif args.command == "delete-without-student-number":
            submissions = client.list_submissions()
            rows = rows_without_valid_student_number(submissions)
            print(f"Rows without a usable student or staff identity: {len(rows)}")
            if rows:
                print_table(rows)
            if args.apply:
                for row in rows:
                    client.delete_submission(row["id"])
                    print(f"Deleted {row['id']}")
        elif args.command == "status-to-zero":
            submissions = client.list_submissions()
            rows = rows_with_legacy_pending_status(submissions)
            print(f"Rows to change to status 0: {len(rows)}")
            if rows:
                print_table(rows)
            if args.apply:
                for row in rows:
                    client.patch_submission(row["id"], {"status": "0"})
                    print(f"Updated {row['id']}")
        elif args.command == "consent-email":
            submissions = client.list_submissions()
            row = write_consent_email(client, submissions, args.id, force=args.force)
            confirm_consent_email_sent(
                client,
                row["id"],
                reminder=bool(row.get("consent_email_sent_at")),
            )
        elif args.command == "target-emails":
            submissions = client.list_submissions()
            print_target_emails(client, submissions, args.id, apply=args.apply)
        elif args.command == "suggest-matches":
            submissions = client.list_submissions()
            groups = suggest_matches(submissions, min_size=args.min_size)
            print_match_groups(groups)

            if args.apply:
                apply_match_groups(client, groups)
        elif args.command == "review-actions":
            submissions = client.list_submissions()
            review_actions(client, submissions)
    except AdminError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


class AdminError(Exception):
    pass


class AdminClient:
    def __init__(self, base_url: str, token: str, insecure: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.context = ssl._create_unverified_context() if insecure else None

    def list_submissions(self) -> list[dict[str, str]]:
        data = self.request("GET", "/api/admin/submissions")
        return data.get("submissions", [])

    def list_connection_requests(self) -> list[dict[str, str]]:
        data = self.request("GET", "/api/admin/connection-requests")
        return data.get("requests", [])

    def delete_submission(self, submission_id: str) -> dict[str, str]:
        path = f"/api/admin/submissions/{quote(submission_id, safe='')}"
        return self.request("DELETE", path)

    def delete_connection_request(self, request_id: str) -> dict[str, str]:
        path = f"/api/admin/connection-requests/{quote(request_id, safe='')}"
        return self.request("DELETE", path)

    def patch_submission(self, submission_id: str, patch: dict[str, str]) -> dict[str, object]:
        path = f"/api/admin/submissions/{quote(submission_id, safe='')}"
        return self.request("PATCH", path, patch)

    def request(self, method: str, path: str, body: dict[str, str] | None = None) -> dict:
        payload = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            f"{self.base_url}{path}",
            data=payload,
            headers=headers,
            method=method,
        )

        try:
            with urlopen(request, timeout=20, context=self.context) as response:
                text = response.read().decode("utf-8")
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise AdminError(f"HTTP {error.code}: {details}") from error
        except URLError as error:
            raise AdminError(str(error.reason)) from error

        return json.loads(text or "{}")


def build_patch(args: argparse.Namespace) -> dict[str, str]:
    patch = {}
    for field in ["direction", "area", "schedule", "status"]:
        value = getattr(args, field)
        if value is not None:
            patch[field] = value
    if args.student_number is not None:
        patch["student_number"] = "".join(char for char in args.student_number if char.isdigit())
    if args.email_address is not None:
        patch["email_address"] = normalize_email_address(args.email_address)
    if args.email_sharing_consent is not None:
        patch["email_sharing_consent"] = args.email_sharing_consent
    if args.connection_requests is not None:
        patch["connection_requests"] = normalize_connected_student_numbers(
            args.connection_requests
        )
    if args.connected_student_numbers is not None:
        patch["connected_student_numbers"] = normalize_connected_student_numbers(
            args.connected_student_numbers
        )
    for field in [
        "consent_token",
        "consent_email_sent_at",
        "consent_email_last_sent_at",
        "consent_reminder_count",
        "consent_response",
        "consent_responded_at",
    ]:
        value = getattr(args, field)
        if value is not None:
            patch[field] = value
    return patch


def write_consent_email(
    client: AdminClient,
    submissions: list[dict[str, str]],
    submission_id: str,
    force: bool = False,
    reminder: bool = False,
) -> dict[str, str]:
    row = find_submission(submissions, submission_id)
    reminder = reminder or bool(row.get("consent_email_sent_at"))
    if reminder and consent_reminder_count(row) >= MAX_CONSENT_REMINDERS:
        raise AdminError("The maximum of three consent reminders has already been sent.")
    if not is_usable_submission(row):
        raise AdminError("This row does not have a usable student or staff identity.")
    if row.get("status") != "1" or not row.get("connection_requests"):
        raise AdminError("Consent email requires a row with status 1 and connection_requests.")
    if row.get("consent_response"):
        raise AdminError("This consent request already has a recorded response.")
    if row.get("consent_email_sent_at") and not row.get("consent_response") and not force:
        raise AdminError(
            "Consent email was already marked as sent at "
            f"{row['consent_email_sent_at']}. Use --force only for an intentional resend."
        )
    student_number = normalize_student_number(row.get("student_number", ""))
    other_awaiting = next((
        other for other in submissions
        if other.get("id") != row.get("id")
        and normalize_student_number(other.get("student_number", "")) == student_number
        and other.get("consent_email_sent_at")
        and not other.get("consent_response")
        and split_student_numbers(other.get("connection_requests", ""))
    ), None)
    if other_awaiting and not force:
        raise AdminError(
            "This person already has another consent email awaiting a response "
            f"(submission {other_awaiting['id']})."
        )

    token = row.get("consent_token")
    if not token:
        token = create_local_token()
        result = client.patch_submission(submission_id, {"consent_token": token})
        row = normalize_submission(result["submission"])

    base_url = os.environ.get("UWC_PUBLIC_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    yes_link = f"{base_url}/consent?token={quote(token, safe='')}&answer=yes"
    no_link = f"{base_url}/consent?token={quote(token, safe='')}&answer=no"
    to_email = submission_email(row)
    subject = (
        "Reminder: UWC Commute Club pool contact request"
        if reminder
        else "UWC Commute Club pool contact request"
    )
    introduction = (
        "This is a reminder that you asked to connect with other people in one of your UWC Commute Club pools."
        if reminder
        else "You asked to connect with other people in one of your UWC Commute Club pools."
    )
    text = "\n".join([
        f"To: {to_email}",
        f"Subject: {subject}",
        "",
        "Hello,",
        "",
        introduction,
        "Before your UWC email address is shared, please choose one of the options below.",
        "The UWC numbers of the other people in the pool are not shown in this email.",
        "You will remain in this pool until you remove yourself via the webpage, so you may receive future connection requests for it.",
        "",
        f"Pool: {format_direction(row['direction'])}, {row['area']}, {format_schedule(row['schedule'])}",
        "",
        safety_and_responsibility_text(),
        "",
        "If you select Yes, your UWC email address may be shared with the relevant people in this pool, and they may contact you directly.",
        "The organisers will not supervise those conversations or any arrangements that follow.",
        "",
        f"Yes, I consent: {yes_link}",
        f"No, I do not consent: {no_link}",
        "",
        "Thank you.",
    ])
    CONSENT_EMAIL_FILE.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    print()
    print(f"Saved consent email to: {CONSENT_EMAIL_FILE}")
    return row


def print_target_emails(
    client: AdminClient,
    submissions: list[dict[str, str]],
    submission_id: str,
    apply: bool = False,
    show_apply_instruction: bool = True,
) -> None:
    row = find_submission(submissions, submission_id)
    if not is_usable_submission(row):
        raise AdminError("This row does not have a usable student or staff identity.")
    if row.get("consent_response") != "yes":
        raise AdminError("Target emails can only be generated after consent_response is yes.")

    pending = split_student_numbers(row.get("connection_requests", ""))
    if not pending:
        raise AdminError("No pending connection_requests remain for this row.")

    requester_email = submission_email(row)
    pool = f"{format_direction(row['direction'])}, {row['area']}, {format_schedule(row['schedule'])}"

    for index, target_sn in enumerate(pending, start=1):
        if index > 1:
            print()
            print("-" * 72)
            print()
        print(f"To: {email_for_uwc_number(target_sn, submissions)}")
        print("Subject: UWC Commute Club pool contact")
        print()
        print("Hello,")
        print()
        print("Another person in one of your UWC Commute Club pools has asked to connect.")
        print("They have consented to share their UWC email address with you.")
        print("You will remain in this pool until you remove yourself via the webpage, so you may receive future connection requests for it.")
        print()
        print(f"Pool: {pool}")
        print(f"You may contact them at: {requester_email}")
        print()
        print("You are not required to respond if you do not want to.")
        print("The pool does not assign driver or passenger roles. Please discuss directly whether anyone can offer a lift and agree on any arrangements.")
        print()
        print_safety_and_responsibility_note()
        print()
        print("Thank you.")

    if not apply:
        if show_apply_instruction:
            print()
            print("Preview only. After sending these emails, rerun with --apply.")
        return

    result = apply_target_emails_sent(client, row)
    print()
    print("Updated after sent emails:")
    print_table([result["submission"]])


def print_safety_and_responsibility_note() -> None:
    print(safety_and_responsibility_text())


def safety_and_responsibility_text() -> str:
    return "\n".join([
        "Safety and responsibility note",
        "",
        "UWC Commute Club only helps people identify others who have expressed interest in similar commute pools.",
        "The organisers do not arrange, supervise, endorse, or take responsibility for any private discussions, travel arrangements, payments, lifts, meetings, or other decisions made between participants after contact details are shared.",
        "",
        "Please use ordinary safety precautions when contacting or travelling with others. For example: meet or discuss arrangements in a public or university setting first; do not share unnecessary personal information; tell someone you trust about any planned lift or meeting; check details carefully before travelling; avoid arrangements that make you uncomfortable; and stop communication if anything feels unsafe or inappropriate.",
        "",
        "Participation is voluntary. You remain responsible for deciding whether to communicate with, meet, or travel with anyone in a pool. You can remove yourself from a pool at any time using the webpage.",
    ])


def local_timestamp() -> str:
    now = datetime.now(SAST)
    return f"{now.strftime('%Y-%m-%d %H:%M:%S')}.{now.microsecond // 1000:03d} SAST"


def parse_stored_timestamp(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise AdminError("Consent email timestamp is missing.")

    try:
        if text.endswith(" SAST"):
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f SAST")
            return parsed.replace(tzinfo=SAST)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(SAST)
    except ValueError as error:
        raise AdminError(f"Invalid consent email timestamp: {text}") from error


def next_consent_reminder_at(row: dict[str, str]) -> datetime:
    first_sent = parse_stored_timestamp(row.get("consent_email_sent_at", ""))
    last_sent = parse_stored_timestamp(
        row.get("consent_email_last_sent_at")
        or row.get("consent_email_sent_at", "")
    )
    elapsed = max(last_sent - first_sent, timedelta(0))
    completed_intervals = int(
        elapsed.total_seconds() // CONSENT_REMINDER_INTERVAL.total_seconds()
    )
    return first_sent + CONSENT_REMINDER_INTERVAL * (completed_intervals + 1)


def consent_reminder_count(row: dict[str, str]) -> int:
    stored = str(row.get("consent_reminder_count", "")).strip()
    if stored:
        try:
            return min(max(int(stored), 0), MAX_CONSENT_REMINDERS)
        except ValueError as error:
            raise AdminError(f"Invalid consent reminder count: {stored}") from error

    first_value = row.get("consent_email_sent_at", "")
    last_value = row.get("consent_email_last_sent_at", "")
    if not first_value or not last_value:
        return 0
    elapsed = max(
        parse_stored_timestamp(last_value) - parse_stored_timestamp(first_value),
        timedelta(0),
    )
    return min(
        int(elapsed.total_seconds() // CONSENT_REMINDER_INTERVAL.total_seconds()),
        MAX_CONSENT_REMINDERS,
    )


def consent_reminder_due(
    row: dict[str, str],
    now: datetime | None = None,
) -> bool:
    return (
        consent_reminder_count(row) < MAX_CONSENT_REMINDERS
        and (now or datetime.now(SAST)) >= next_consent_reminder_at(row)
    )


def format_local_datetime(value: datetime) -> str:
    return f"{value.astimezone(SAST).strftime('%Y-%m-%d %H:%M:%S')} SAST"


def confirm_consent_email_sent(
    client: AdminClient,
    submission_id: str,
    reminder: bool = False,
) -> bool:
    if not ask_yes_no("Have you sent this consent email?"):
        print("Not marked as sent. The file will be offered again during the next review.")
        return False

    sent_at = local_timestamp()
    patch = {"consent_email_last_sent_at": sent_at}
    if reminder:
        submissions = client.list_submissions()
        row = find_submission(submissions, submission_id)
        patch["consent_reminder_count"] = str(consent_reminder_count(row) + 1)
    else:
        patch["consent_email_sent_at"] = sent_at
        patch["consent_reminder_count"] = "0"
    result = client.patch_submission(submission_id, patch)
    CONSENT_EMAIL_FILE.unlink(missing_ok=True)
    label = "reminder" if reminder else "consent email"
    print(f"Marked {label} as sent at {sent_at}.")
    print(f"Removed local consent email file: {CONSENT_EMAIL_FILE}")
    print_table([result["submission"]])
    return True


def apply_target_emails_sent(
    client: AdminClient,
    row: dict[str, str],
) -> dict[str, object]:
    pending = split_student_numbers(row.get("connection_requests", ""))
    existing = set(split_student_numbers(row.get("connected_student_numbers", "")))
    existing.update(pending)
    return client.patch_submission(row["id"], {
        "connection_requests": "",
        "connected_student_numbers": normalize_connected_student_numbers("|".join(sorted(existing))),
        "status": "2",
    })


def review_actions(client: AdminClient, submissions: list[dict[str, str]]) -> None:
    rows = [
        normalize_submission(row)
        for row in submissions
        if is_usable_submission(normalize_submission(row))
    ]
    awaiting_consent = [
        row for row in rows
        if row.get("status") == "1"
        and split_student_numbers(row.get("connection_requests", ""))
        and not row.get("consent_response")
        and row.get("consent_email_sent_at")
    ]
    due_reminders = [
        row for row in awaiting_consent
        if consent_reminder_due(row)
    ]
    reminder_limit_reached = [
        row for row in awaiting_consent
        if consent_reminder_count(row) >= MAX_CONSENT_REMINDERS
    ]
    consent_candidates = [
        row for row in rows
        if row.get("status") == "1"
        and split_student_numbers(row.get("connection_requests", ""))
        and not row.get("consent_response")
        and not row.get("consent_email_sent_at")
    ]
    unavailable_people = {
        normalize_student_number(row.get("student_number", ""))
        for row in awaiting_consent
    }
    pending_consent = []
    deferred_consent = []
    for row in sorted(consent_candidates, key=lambda item: item.get("submitted_at", "")):
        student_number = normalize_student_number(row.get("student_number", ""))
        if student_number in unavailable_people:
            deferred_consent.append(row)
            continue
        pending_consent.append(row)
        unavailable_people.add(student_number)
    approved_targets = [
        row for row in rows
        if row.get("consent_response") == "yes"
        and split_student_numbers(row.get("connection_requests", ""))
    ]
    rejected_requests = [
        row for row in rows
        if row.get("consent_response") == "no"
    ]
    unattended_groups = unrequested_multi_person_pools(rows)

    print("Review actions")
    print("==============")
    print(f"Unsent consent emails: {len(pending_consent)}")
    print(f"Awaiting consent responses: {len(awaiting_consent)}")
    print(f"Consent reminders due: {len(due_reminders)}")
    print(f"Consent reminder limit reached: {len(reminder_limit_reached)}")
    print(f"Deferred consent emails for people already awaiting: {len(deferred_consent)}")
    print(f"Approved target email batches: {len(approved_targets)}")
    print(f"Rejected consent rows needing cleanup: {len(rejected_requests)}")
    print(f"Multi-person pools with no pending consent row: {len(unattended_groups)}")

    if pending_consent:
        print()
        print("Unsent consent emails")
        print("---------------------")
        for row in pending_consent:
            print()
            print_action_row(row)
            if ask_yes_no("Generate this consent email now?"):
                print()
                write_consent_email(client, rows, row["id"])
                if not confirm_consent_email_sent(client, row["id"]):
                    return

    if due_reminders:
        print()
        print("Consent reminders due")
        print("---------------------")
        for row in due_reminders:
            print()
            print_action_row(row)
            print(f"  First sent: {row.get('consent_email_sent_at', '')}")
            print(f"  Last sent: {row.get('consent_email_last_sent_at') or row.get('consent_email_sent_at', '')}")
            print(f"  Reminders sent: {consent_reminder_count(row)} of {MAX_CONSENT_REMINDERS}")
            if ask_yes_no("Generate this consent reminder now?"):
                print()
                write_consent_email(
                    client,
                    rows,
                    row["id"],
                    force=True,
                    reminder=True,
                )
                if not confirm_consent_email_sent(client, row["id"], reminder=True):
                    return

    if awaiting_consent:
        print()
        print("Awaiting consent responses")
        print("--------------------------")
        print("These rows are awaiting responses. Up to three reminders are offered at three-day intervals.")
        for row in awaiting_consent:
            print_action_row(row)
            print(f"  First sent: {row.get('consent_email_sent_at', '')}")
            print(f"  Last sent: {row.get('consent_email_last_sent_at') or row.get('consent_email_sent_at', '')}")
            count = consent_reminder_count(row)
            print(f"  Reminders sent: {count} of {MAX_CONSENT_REMINDERS}")
            if count >= MAX_CONSENT_REMINDERS:
                print("  Next reminder: none (limit reached)")
            else:
                print(f"  Next reminder: {format_local_datetime(next_consent_reminder_at(row))}")

    if deferred_consent:
        print()
        print("Deferred consent emails")
        print("-----------------------")
        print("These will remain deferred until the same person's earlier consent request receives a response.")
        for row in deferred_consent:
            print_action_row(row)

    if approved_targets:
        print()
        print("Approved target emails")
        print("----------------------")
        for row in approved_targets:
            print()
            print_action_row(row)
            pending = split_student_numbers(row.get("connection_requests", ""))
            print(f"Targets: {', '.join(pending)}")
            if ask_yes_no("Generate target emails for this approved request?"):
                print()
                print_target_emails(
                    client,
                    rows,
                    row["id"],
                    apply=False,
                    show_apply_instruction=False,
                )
                if ask_yes_no("Have you sent these target emails and want to mark them connected?"):
                    result = apply_target_emails_sent(client, row)
                    print("Updated:")
                    print_table([result["submission"]])

    if rejected_requests:
        print()
        print("Rejected consent")
        print("----------------")
        for row in rejected_requests:
            print()
            print_action_row(row)
            if ask_yes_no("Generate the pool-removal notification email?"):
                print()
                write_removal_email(row)
                if ask_yes_no("Have you sent this removal notification and want to delete the pool row?"):
                    client.delete_submission(row["id"])
                    REMOVAL_EMAIL_FILE.unlink(missing_ok=True)
                    print(f"Deleted {row['id']}")
                    print(f"Removed local removal email file: {REMOVAL_EMAIL_FILE}")

    if unattended_groups:
        print()
        print("Sanity check: multi-person pools without pending consent")
        print("-------------------------------------------------------")
        for index, group in enumerate(unattended_groups, start=1):
            requester = group["requester"]
            targets = group["targets"]
            target_numbers = [
                normalize_student_number(row.get("student_number", ""))
                for row in targets
            ]
            print(
                f"{index}. {format_direction(group['direction'])}, {group['area']}, "
                f"{format_schedule(group['schedule'])}"
            )
            print(f"   Suggested requester: {requester.get('student_number', '')} ({requester.get('submitted_at', '')})")
            print(f"   Targets: {', '.join(target_numbers)}")
            if ask_yes_no("Start a consent request from the latest student to the earlier pool members?"):
                result = client.patch_submission(requester["id"], {
                    "status": "1",
                    "connection_requests": normalize_connected_student_numbers("|".join(target_numbers)),
                    "consent_email_sent_at": "",
                    "consent_email_last_sent_at": "",
                    "consent_reminder_count": "0",
                    "consent_response": "",
                    "consent_responded_at": "",
                })
                print("Updated:")
                print_table([result["submission"]])
                if ask_yes_no("Generate this consent email now?"):
                    print()
                    updated_requester = normalize_submission(result["submission"])
                    updated_rows = [
                        updated_requester if row["id"] == requester["id"] else row
                        for row in rows
                    ]
                    write_consent_email(client, updated_rows, requester["id"])
                    if not confirm_consent_email_sent(client, requester["id"]):
                        return

    if not any([
        pending_consent,
        due_reminders,
        approved_targets,
        rejected_requests,
        unattended_groups,
    ]):
        print()
        print("No action is required right now.")


def print_action_row(row: dict[str, str]) -> None:
    print(
        f"{row['id']} | {row.get('student_number', '')} | "
        f"{format_direction(row.get('direction', ''))}, {row.get('area', '')}, "
        f"{format_schedule(row.get('schedule', ''))}"
    )


def write_removal_email(row: dict[str, str]) -> None:
    if row.get("consent_response") != "no":
        raise AdminError("A removal notification requires consent_response to be no.")

    text = "\n".join([
        f"To: {submission_email(row)}",
        "Subject: UWC Commute Club pool removal",
        "",
        "Hello,",
        "",
        "You chose not to consent to sharing your UWC email address with other people in this pool:",
        "",
        f"Pool: {format_direction(row['direction'])}, {row['area']}, {format_schedule(row['schedule'])}",
        "",
        "Your interest in this pool has therefore been removed from the UWC Commute Club database.",
        "Your contact details were not shared.",
        "",
        "This does not affect any of your other pool interests. You may add this pool again later through the webpage if you change your mind.",
        "",
        "Thank you.",
    ])
    REMOVAL_EMAIL_FILE.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    print()
    print(f"Saved removal email to: {REMOVAL_EMAIL_FILE}")


def ask_yes_no(question: str) -> bool:
    answer = input(f"{question} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def unrequested_multi_person_pools(
    submissions: list[dict[str, str]],
) -> list[dict[str, object]]:
    active = [
        row for row in submissions
        if row.get("status", "0") in {"0", "1", "2", "pending", ""}
        and row.get("consent_response") != "no"
        and row.get("direction") in {"to_uwc", "from_uwc"}
        and row.get("area")
        and is_usable_submission(row)
    ]
    by_pool: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)

    for row in active:
        for cell in schedule_cells(row):
            key = (row["direction"], normalize_area_key(row["area"]), cell)
            by_pool[key].append(row)

    groups = []
    for (direction, area_key, schedule), rows in by_pool.items():
        rows_by_student = {}
        for row in sorted(rows, key=lambda item: item.get("submitted_at", "")):
            rows_by_student[normalize_student_number(row.get("student_number", ""))] = row
        unique_rows = list(rows_by_student.values())
        has_pending = any(
            split_student_numbers(row.get("connection_requests", ""))
            for row in unique_rows
        )
        if len(unique_rows) < 2 or has_pending:
            continue
        sorted_rows = sorted(unique_rows, key=lambda item: item.get("submitted_at", ""))
        requester = sorted_rows[-1]
        groups.append({
            "direction": direction,
            "area": display_area(area_key, sorted_rows),
            "schedule": schedule,
            "requester": requester,
            "targets": sorted_rows[:-1],
            "rows": sorted_rows,
        })

    return sorted(
        groups,
        key=lambda group: (
            str(group["direction"]),
            str(group["area"]),
            str(group["schedule"]),
        ),
    )


def update_connected_students(
    client: AdminClient,
    submissions: list[dict[str, str]],
    args: argparse.Namespace,
) -> dict[str, object]:
    row = find_submission(submissions, args.id)
    existing = set(split_student_numbers(row.get("connected_student_numbers", "")))
    pending = set(split_student_numbers(row.get("connection_requests", "")))

    if args.add is not None:
        additions = set(split_student_numbers(normalize_connected_student_numbers("|".join(args.add))))
        existing.update(additions)
        pending.difference_update(additions)
    elif args.remove is not None:
        existing.difference_update(
            split_student_numbers(normalize_connected_student_numbers("|".join(args.remove)))
        )
    else:
        existing = set(
            split_student_numbers(normalize_connected_student_numbers("|".join(args.set)))
        )

    connected = normalize_connected_student_numbers("|".join(sorted(existing)))
    patch = {"connected_student_numbers": connected}
    if args.add is not None:
        patch["connection_requests"] = normalize_connected_student_numbers("|".join(sorted(pending)))
        if not pending:
            patch["status"] = "2"
    return client.patch_submission(args.id, patch)


def find_submission(submissions: list[dict[str, str]], submission_id: str) -> dict[str, str]:
    for row in submissions:
        if row.get("id") == submission_id:
            return normalize_submission(row)
    raise AdminError(f"Submission not found: {submission_id}")


def rows_without_valid_student_number(submissions: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        normalize_submission(row)
        for row in submissions
        if not is_usable_submission(normalize_submission(row))
    ]


def rows_with_legacy_pending_status(submissions: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        normalize_submission(row)
        for row in submissions
        if row.get("status", "") in {"", "pending"}
    ]


def dedupe_plan(submissions: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    seen = set()
    delete_rows = []
    patch_rows = []

    for row in submissions:
        cells = sorted(schedule_cells(row))
        if not cells:
            delete_rows.append(row)
            continue

        keep_cells = []
        for cell in cells:
            key = interest_key(row, cell)
            if key in seen:
                continue
            seen.add(key)
            keep_cells.append(cell)

        if not keep_cells:
            delete_rows.append(row)
            continue

        normalized_schedule = "|".join(keep_cells)
        if row.get("schedule") != normalized_schedule:
            patch_rows.append({**row, "schedule": normalized_schedule})

    return {"delete": delete_rows, "patch": patch_rows}


def print_dedupe_plan(plan: dict[str, list[dict[str, str]]]) -> None:
    print(f"Rows to update: {len(plan['patch'])}")
    if plan["patch"]:
        print_table(plan["patch"])

    print()
    print(f"Rows to delete: {len(plan['delete'])}")
    if plan["delete"]:
        print_table(plan["delete"])


def apply_dedupe_plan(client: AdminClient, plan: dict[str, list[dict[str, str]]]) -> None:
    for row in plan["patch"]:
        client.patch_submission(row["id"], {"schedule": row["schedule"]})
        print(f"Updated {row['id']}")

    for row in plan["delete"]:
        client.delete_submission(row["id"])
        print(f"Deleted {row['id']}")


def print_table(rows: list[dict[str, str]], fields: list[str] | None = None) -> None:
    if not rows:
        print("No submissions.")
        return

    fields = fields or FIELDS
    rows = [normalize_submission(row) for row in rows]

    widths = {
        field: min(
            max(len(field), *(len(display(row.get(field, ""))) for row in rows)),
            40,
        )
        for field in fields
    }

    header = "  ".join(field.ljust(widths[field]) for field in fields)
    print(header)
    print("  ".join("-" * widths[field] for field in fields))

    for row in rows:
        print("  ".join(display(row.get(field, ""))[: widths[field]].ljust(widths[field]) for field in fields))


def display(value: str) -> str:
    return str(value).replace("\n", " ").strip()


def normalize_student_number(value: str) -> str:
    return "".join(char for char in str(value) if char.isdigit())


def is_valid_student_number(value: str) -> bool:
    return len(normalize_student_number(value)) == 7


def is_valid_staff_number(value: str) -> bool:
    return len(normalize_student_number(value)) == 6


def is_valid_uwc_number(value: str) -> bool:
    return is_valid_student_number(value) or is_valid_staff_number(value)


def normalize_email_address(value: str) -> str:
    return str(value or "").strip().lower()


def is_valid_uwc_email(value: str) -> bool:
    email = normalize_email_address(value)
    local, separator, domain = email.rpartition("@")
    return bool(separator and local and domain == "uwc.ac.za" and " " not in email)


def is_usable_submission(row: dict[str, str]) -> bool:
    number = row.get("student_number", "")
    if is_valid_student_number(number):
        return True
    return (
        is_valid_staff_number(number)
        and is_valid_uwc_email(row.get("email_address", ""))
        and row.get("email_sharing_consent") == "yes"
    )


def split_student_numbers(value: str) -> list[str]:
    return [
        normalize_student_number(part)
        for part in str(value or "").split("|")
        if is_valid_uwc_number(part)
    ]


def normalize_connected_student_numbers(value: str) -> str:
    parts = [part for part in str(value or "").split("|") if part.strip()]
    invalid = [part for part in parts if not is_valid_uwc_number(part)]
    if invalid:
        raise AdminError(
            "Connected UWC numbers must be 6- or 7-digit numbers separated by |. "
            f"Invalid: {', '.join(invalid)}"
        )
    return "|".join(sorted(set(normalize_student_number(part) for part in parts)))


def create_local_token() -> str:
    return secrets.token_hex(24)


def student_email(student_number: str) -> str:
    return f"{normalize_student_number(student_number)}@myuwc.ac.za"


def submission_email(row: dict[str, str]) -> str:
    number = row.get("student_number", "")
    if is_valid_student_number(number):
        return student_email(number)
    if is_usable_submission(row):
        return normalize_email_address(row.get("email_address", ""))
    raise AdminError("No usable UWC email address is available for this row.")


def email_for_uwc_number(
    uwc_number: str,
    submissions: list[dict[str, str]],
) -> str:
    if is_valid_student_number(uwc_number):
        return student_email(uwc_number)
    normalized = normalize_student_number(uwc_number)
    for row in submissions:
        candidate = normalize_submission(row)
        if (
            normalize_student_number(candidate.get("student_number", "")) == normalized
            and is_usable_submission(candidate)
        ):
            return submission_email(candidate)
    raise AdminError(f"No usable UWC email address is available for staff number {normalized}.")


def format_direction(direction: str) -> str:
    return "To UWC" if direction == "to_uwc" else "From UWC"


def format_schedule(schedule: str) -> str:
    days = {
        "mon": "Monday",
        "tue": "Tuesday",
        "wed": "Wednesday",
        "thu": "Thursday",
        "fri": "Friday",
    }
    day, _, time = schedule.partition("@")
    return f"{days.get(day, day)} {time}".strip()


def suggest_matches(
    submissions: list[dict[str, str]],
    min_size: int = 2,
) -> list[dict[str, object]]:
    submissions = [normalize_submission(submission) for submission in submissions]
    active = [
        submission
        for submission in submissions
        if submission.get("status", "0") in {"0", "1", "2", "pending", ""}
        and submission.get("direction") in {"to_uwc", "from_uwc"}
        and submission.get("area")
        and is_usable_submission(submission)
    ]
    buckets: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

    for submission in active:
        key = (submission["direction"], normalize_area_key(submission["area"]))
        buckets[key].append(submission)

    groups = []
    for (direction, area_key), rows in buckets.items():
        rows = sorted(rows, key=lambda row: row.get("submitted_at", ""))
        schedule_map: dict[str, list[dict[str, str]]] = defaultdict(list)

        for row in rows:
            for cell in schedule_cells(row):
                schedule_map[cell].append(row)

        neighbors: dict[str, set[str]] = {row["id"]: set() for row in rows}
        by_id = {row["id"]: row for row in rows}

        for cell_rows in schedule_map.values():
            ids = [row["id"] for row in cell_rows]
            for row_id in ids:
                neighbors[row_id].update(other_id for other_id in ids if other_id != row_id)

        visited = set()
        for row in rows:
            if row["id"] in visited:
                continue

            component_ids = collect_component(row["id"], neighbors)
            visited.update(component_ids)
            if len(component_ids) < min_size:
                continue

            cell_rows = [by_id[row_id] for row_id in component_ids]
            common_cells = set.intersection(*(schedule_cells(cell_row) for cell_row in cell_rows))
            shared_schedule = "|".join(sorted(common_cells)) if common_cells else "overlapping times"

            groups.append({
                "direction": direction,
                "area": display_area(area_key, cell_rows),
                "shared_schedule": shared_schedule,
                "rows": sorted(cell_rows, key=lambda item: item.get("submitted_at", "")),
            })

    return sorted(
        groups,
        key=lambda group: (
            str(group["direction"]),
            str(group["area"]),
            str(group["shared_schedule"]),
        ),
    )


def print_match_groups(groups: list[dict[str, object]]) -> None:
    if not groups:
        print("No suggested matches.")
        return

    for index, group in enumerate(groups, start=1):
        rows = group["rows"]
        print()
        print(
            f"Group {index}: {group['direction']} | {group['area']} | "
            f"shared time {group['shared_schedule']} | {len(rows)} people"
        )
        print_table(rows)


def apply_match_groups(client: AdminClient, groups: list[dict[str, object]]) -> None:
    if not groups:
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    for index, group in enumerate(groups, start=1):
        group_id = f"group_{stamp}_{index}"
        print()
        print(f"Applying {group_id}")

        for row in group["rows"]:
            result = client.patch_submission(
                row["id"],
                {"status": "matched"},
            )
            submission = result["submission"]
            print(f"  {submission['id']} -> matched ({group_id})")


def schedule_cells(row: dict[str, str]) -> set[str]:
    return {cell for cell in row.get("schedule", "").split("|") if cell}


def interest_key(row: dict[str, str], schedule: str) -> tuple[str, str, str, str]:
    row = normalize_submission(row)
    return (
        row.get("direction", ""),
        normalize_area_key(row.get("area", "")),
        schedule,
        row.get("student_number") or row.get("pilot_code") or row.get("nickname") or row.get("id", ""),
    )


def normalize_submission(row: dict[str, str]) -> dict[str, str]:
    if row.get("student_number"):
        return row
    return {**row, "student_number": row.get("pilot_code") or row.get("nickname", "")}


def collect_component(start_id: str, neighbors: dict[str, set[str]]) -> set[str]:
    component = set()
    stack = [start_id]

    while stack:
        row_id = stack.pop()
        if row_id in component:
            continue
        component.add(row_id)
        stack.extend(neighbors[row_id] - component)

    return component


def normalize_area_key(area: str) -> str:
    return " ".join(area.lower().split())


def display_area(area_key: str, rows: list[dict[str, str]]) -> str:
    return next((row.get("area", "") for row in rows if normalize_area_key(row.get("area", "")) == area_key), area_key)


if __name__ == "__main__":
    raise SystemExit(main())
