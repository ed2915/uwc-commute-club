#!/usr/bin/env python3
"""Inspect and maintain UWC Commute Club submissions on Render."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://uwc-commute-club.onrender.com"
FIELDS = [
    "id",
    "submitted_at",
    "direction",
    "area",
    "schedule",
    "nickname",
    "status",
    "matched_group_id",
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
    subparsers.add_parser("json", help="Print raw JSON.")

    delete_parser = subparsers.add_parser("delete", help="Delete a submission by id.")
    delete_parser.add_argument("id")

    patch_parser = subparsers.add_parser("set", help="Update fields for a submission.")
    patch_parser.add_argument("id")
    patch_parser.add_argument("--direction", choices=["to_uwc", "from_uwc"])
    patch_parser.add_argument("--area")
    patch_parser.add_argument("--schedule", help="Example: mon@07:00|wed@08:30")
    patch_parser.add_argument("--nickname")
    patch_parser.add_argument(
        "--status",
        choices=["pending", "matched", "deleted", "archived"],
    )
    patch_parser.add_argument("--matched-group-id")

    args = parser.parse_args()

    if not args.token:
        print("Set UWC_ADMIN_TOKEN or pass --token.", file=sys.stderr)
        return 2

    client = AdminClient(args.base_url, args.token, insecure=args.insecure)

    try:
        if args.command == "list":
            submissions = client.list_submissions()
            print_table(submissions)
        elif args.command == "json":
            print(json.dumps(client.list_submissions(), indent=2))
        elif args.command == "delete":
            result = client.delete_submission(args.id)
            print(f"Deleted {result.get('deleted', args.id)}")
        elif args.command == "set":
            patch = build_patch(args)
            if not patch:
                print("Provide at least one field to update.", file=sys.stderr)
                return 2
            result = client.patch_submission(args.id, patch)
            print("Updated:")
            print_table([result["submission"]])
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

    def delete_submission(self, submission_id: str) -> dict[str, str]:
        path = f"/api/admin/submissions/{quote(submission_id, safe='')}"
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
    for field in ["direction", "area", "schedule", "nickname", "status"]:
        value = getattr(args, field)
        if value is not None:
            patch[field] = value
    if args.matched_group_id is not None:
        patch["matched_group_id"] = args.matched_group_id
    return patch


def print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("No submissions.")
        return

    widths = {
        field: min(
            max(len(field), *(len(display(row.get(field, ""))) for row in rows)),
            40,
        )
        for field in FIELDS
    }

    header = "  ".join(field.ljust(widths[field]) for field in FIELDS)
    print(header)
    print("  ".join("-" * widths[field] for field in FIELDS))

    for row in rows:
        print("  ".join(display(row.get(field, ""))[: widths[field]].ljust(widths[field]) for field in FIELDS))


def display(value: str) -> str:
    return str(value).replace("\n", " ").strip()


if __name__ == "__main__":
    raise SystemExit(main())
