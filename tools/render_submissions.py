#!/usr/bin/env python3
"""Inspect and maintain UWC Commute Club submissions on Render."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
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
    "student_number",
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
    dedupe_parser = subparsers.add_parser(
        "dedupe",
        help="Remove duplicate student-number/route/time interests from existing rows.",
    )
    dedupe_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the dedupe plan. Without this, only prints what would change.",
    )

    delete_parser = subparsers.add_parser("delete", help="Delete a submission by id.")
    delete_parser.add_argument("id")

    patch_parser = subparsers.add_parser("set", help="Update fields for a submission.")
    patch_parser.add_argument("id")
    patch_parser.add_argument("--direction", choices=["to_uwc", "from_uwc"])
    patch_parser.add_argument("--area")
    patch_parser.add_argument("--schedule", help="Example: mon@07:00|wed@08:30")
    patch_parser.add_argument("--student-number")
    patch_parser.add_argument(
        "--status",
        choices=["pending", "matched", "deleted", "archived"],
    )
    patch_parser.add_argument("--matched-group-id")

    suggest_parser = subparsers.add_parser(
        "suggest-matches",
        help="Suggest pending groups with the same direction, area, and overlapping schedule.",
    )
    suggest_parser.add_argument(
        "--min-size",
        type=int,
        default=2,
        help="Minimum number of submissions in a suggested group. Default: 2",
    )
    suggest_parser.add_argument(
        "--apply",
        action="store_true",
        help="Mark suggested groups as matched with generated group ids.",
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
        elif args.command == "json":
            print(json.dumps(client.list_submissions(), indent=2))
        elif args.command == "delete":
            result = client.delete_submission(args.id)
            print(f"Deleted {result.get('deleted', args.id)}")
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
        elif args.command == "suggest-matches":
            submissions = client.list_submissions()
            groups = suggest_matches(submissions, min_size=args.min_size)
            print_match_groups(groups)

            if args.apply:
                apply_match_groups(client, groups)
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
    for field in ["direction", "area", "schedule", "status"]:
        value = getattr(args, field)
        if value is not None:
            patch[field] = value
    if args.student_number is not None:
        patch["student_number"] = "".join(char for char in args.student_number if char.isdigit())[:12]
    if args.matched_group_id is not None:
        patch["matched_group_id"] = args.matched_group_id
    return patch


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


def print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("No submissions.")
        return

    rows = [normalize_submission(row) for row in rows]

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


def suggest_matches(
    submissions: list[dict[str, str]],
    min_size: int = 2,
) -> list[dict[str, object]]:
    submissions = [normalize_submission(submission) for submission in submissions]
    pending = [
        submission
        for submission in submissions
        if submission.get("status", "pending") == "pending"
        and submission.get("direction") in {"to_uwc", "from_uwc"}
        and submission.get("area")
    ]
    buckets: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

    for submission in pending:
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
                {"status": "matched", "matched_group_id": group_id},
            )
            submission = result["submission"]
            print(f"  {submission['id']} -> matched")


def schedule_cells(row: dict[str, str]) -> set[str]:
    return {cell for cell in row.get("schedule", "").split("|") if cell}


def interest_key(row: dict[str, str], schedule: str) -> tuple[str, str, str, str]:
    row = normalize_submission(row)
    return (
        row.get("direction", ""),
        normalize_area_key(row.get("area", "")),
        schedule,
        row.get("student_number", ""),
    )


def normalize_submission(row: dict[str, str]) -> dict[str, str]:
    if row.get("student_number"):
        return row
    return {**row, "student_number": row.get("nickname", "")}


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
