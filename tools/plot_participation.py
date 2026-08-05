#!/usr/bin/env python3
"""Plot active UWC Commute Club participation day by day."""

from __future__ import annotations

import argparse
import math
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

import matplotlib
import matplotlib.dates as mdates

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, MultipleLocator

from render_submissions import (
    AdminClient,
    AdminError,
    DEFAULT_BASE_URL,
    is_usable_submission,
)


SAST = ZoneInfo("Africa/Johannesburg")
ACTIVE_STATUSES = {"0", "1", "2"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plot cumulative active users and pool interests from the live "
            "UWC Commute Club database."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("UWC_ADMIN_BASE_URL", DEFAULT_BASE_URL),
        help=f"Render service URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("UWC_ADMIN_TOKEN"),
        help="Admin token. Prefer setting UWC_ADMIN_TOKEN.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for this request.",
    )
    parser.add_argument(
        "--start",
        type=parse_date_argument,
        help="First date to display, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--end",
        type=parse_date_argument,
        help="Last date to display, in YYYY-MM-DD format. Default: today.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/participation_over_time.png"),
        help="Output PNG, PDF, or SVG path. Default: reports/participation_over_time.png",
    )
    args = parser.parse_args()

    if not args.token:
        print("Set UWC_ADMIN_TOKEN or pass --token.", file=sys.stderr)
        return 2

    client = AdminClient(args.base_url, args.token, insecure=args.insecure)

    try:
        submissions = client.list_submissions()
        events, skipped = participation_events(submissions)
    except AdminError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not events:
        print("No active submissions with valid timestamps were found.", file=sys.stderr)
        return 1

    start = args.start or min(event["day"] for event in events)
    end = args.end or datetime.now(SAST).date()

    if end < start:
        print("--end cannot be earlier than --start.", file=sys.stderr)
        return 2

    days, user_counts, interest_counts = daily_cumulative_counts(events, start, end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    draw_chart(days, user_counts, interest_counts, args.output)

    print(f"Saved chart: {args.output.resolve()}")
    print(f"Displayed period: {start.isoformat()} to {end.isoformat()}")
    print(f"Active unique users at end: {user_counts[-1]}")
    print(f"Active pool interests at end: {interest_counts[-1]}")
    if skipped:
        print(f"Skipped rows with missing or invalid timestamps: {skipped}")
    print(
        "Note: deleted pool interests are no longer in the database, so this "
        "reconstructs growth from records that are active now."
    )
    return 0


def parse_date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD.") from error


def participation_events(
    submissions: list[dict[str, str]],
) -> tuple[list[dict[str, object]], int]:
    events = []
    skipped = 0

    for row in submissions:
        status = normalize_status(row.get("status", ""))
        if status not in ACTIVE_STATUSES:
            continue

        if not is_usable_submission(row):
            continue

        direction = str(row.get("direction", "")).strip()
        area = normalize_area(row.get("area", ""))
        if direction not in {"to_uwc", "from_uwc"} or not area:
            continue

        submitted_day = parse_submission_day(row.get("submitted_at", ""))
        if submitted_day is None:
            skipped += 1
            continue

        identity = identity_key(
            row.get("student_number")
            or row.get("pilot_code")
            or row.get("nickname")
            or row.get("id")
        )
        if not identity:
            continue

        interest_keys = {
            "|".join((direction, area.lower(), schedule, identity))
            for schedule in schedule_cells(row)
        }
        events.append({
            "day": submitted_day,
            "identity": identity,
            "interest_keys": interest_keys,
        })

    return sorted(events, key=lambda event: event["day"]), skipped


def parse_submission_day(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None

    try:
        if text.endswith(" SAST"):
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f SAST")
            return parsed.replace(tzinfo=SAST).date()

        iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(iso_text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SAST)
        return parsed.astimezone(SAST).date()
    except ValueError:
        return None


def normalize_status(value: object) -> str:
    status = str(value or "").strip()
    return "0" if status in {"", "pending"} else status


def normalize_area(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def identity_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def schedule_cells(row: dict[str, str]) -> set[str]:
    return {
        value.strip()
        for value in str(row.get("schedule", "")).split("|")
        if value.strip()
    }


def daily_cumulative_counts(
    events: list[dict[str, object]],
    start: date,
    end: date,
) -> tuple[list[date], list[int], list[int]]:
    days = []
    user_counts = []
    interest_counts = []
    active_users: set[str] = set()
    active_interests: set[str] = set()
    event_index = 0
    current_day = start

    while current_day <= end:
        while event_index < len(events) and events[event_index]["day"] <= current_day:
            event = events[event_index]
            active_users.add(str(event["identity"]))
            active_interests.update(event["interest_keys"])
            event_index += 1

        days.append(current_day)
        user_counts.append(len(active_users))
        interest_counts.append(len(active_interests))
        current_day += timedelta(days=1)

    return days, user_counts, interest_counts


def draw_chart(
    days: list[date],
    user_counts: list[int],
    interest_counts: list[int],
    output: Path,
) -> None:
    figure, axis = plt.subplots(figsize=(12, 6.8))
    figure.patch.set_facecolor("#f3f6fb")
    axis.set_facecolor("#ffffff")
    figure.subplots_adjust(left=0.09, right=0.96, top=0.86, bottom=0.19)

    axis.plot(
        days,
        interest_counts,
        color="#123b79",
        linewidth=2.8,
        marker="o",
        markersize=4,
        drawstyle="steps-post",
        zorder=3,
        label="Active pool interests",
    )
    axis.plot(
        days,
        user_counts,
        color="#b18b2e",
        linewidth=2.8,
        linestyle="--",
        marker="o",
        markersize=4,
        drawstyle="steps-post",
        zorder=4,
        label="Active unique users",
    )

    axis.set_title(
        "UWC Commute Club Participation",
        color="#08275a",
        fontsize=18,
        fontweight="bold",
        loc="left",
        pad=18,
    )
    axis.set_ylabel("Number active", fontweight="bold")
    axis.set_xlabel(
        "Submission date (South African time)",
        fontweight="bold",
        labelpad=12,
    )

    maximum = max(max(user_counts), max(interest_counts), 1)
    axis.set_ylim(0, max(2, math.ceil(maximum * 1.15)))
    if maximum <= 15:
        axis.yaxis.set_major_locator(MultipleLocator(1))
    else:
        axis.yaxis.set_major_locator(MaxNLocator(integer=True, min_n_ticks=4))

    tick_interval = max(1, math.ceil(len(days) / 10))
    date_locator = mdates.DayLocator(interval=tick_interval)
    axis.xaxis.set_major_locator(date_locator)
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    axis.margins(x=0.03)

    axis.grid(axis="y", color="#d6deea", linewidth=0.9)
    axis.grid(axis="x", visible=False)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.spines["left"].set_color("#9ca8b8")
    axis.spines["bottom"].set_color("#9ca8b8")
    axis.legend(frameon=False, loc="upper left")

    axis.annotate(
        str(interest_counts[-1]),
        (days[-1], interest_counts[-1]),
        xytext=(8, 7),
        textcoords="offset points",
        color="#123b79",
        fontweight="bold",
    )
    axis.annotate(
        str(user_counts[-1]),
        (days[-1], user_counts[-1]),
        xytext=(8, -15),
        textcoords="offset points",
        color="#8a6a1f",
        fontweight="bold",
    )

    figure.text(
        0.01,
        0.005,
        (
            "Based on records that are active in the database now. "
            "Previously removed interests cannot be reconstructed."
        ),
        color="#5d6675",
        fontsize=9,
    )
    figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
    plt.close(figure)


if __name__ == "__main__":
    raise SystemExit(main())
