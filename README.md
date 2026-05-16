# UWC Commute Club

A small web-first app for grouping University of the Western Cape commuters by area, day, and travel time.

## Part 1

This first slice captures trip submissions from a mobile-friendly webpage and stores them in `data/submissions.csv`.

Run it with:

```sh
npm start
```

Then open:

```text
http://localhost:3000
```

## Render Deployment

This app can run on a low-cost Render web service with a persistent disk. The included `render.yaml` config uses:

- Node web service
- `starter` instance type
- 1 GB persistent disk
- disk mount path: `/var/data`
- `DATA_DIR=/var/data`

Render web services with persistent disks must use a paid instance type. Free web services do not support persistent disks, and their local files are ephemeral.

Manual Render settings:

- Service type: Web Service
- Build command: `npm install`
- Start command: `npm start`
- Environment variable: `DATA_DIR=/var/data`
- Add disk:
  - Name: `submissions`
  - Mount path: `/var/data`
  - Size: `1 GB`

The app binds to Render's provided `PORT` and stores `submissions.csv` inside `DATA_DIR`.

## Admin Submissions Tool

The Render CSV can be inspected and maintained with the local Python script:

```sh
export UWC_ADMIN_TOKEN="use-the-same-value-as-render-admin-token"
python3 tools/render_submissions.py list
python3 tools/render_submissions.py requests
python3 tools/render_submissions.py suggest-matches
python3 tools/render_submissions.py dedupe
python3 tools/render_submissions.py status-to-zero
python3 tools/render_submissions.py set sub_abc123 --status matched
python3 tools/render_submissions.py consent-email sub_abc123
python3 tools/render_submissions.py connect sub_abc123 --add 7654321 2345678
python3 tools/render_submissions.py delete sub_abc123
```

New submissions are stored as one interest row per selected pool. If a student
or staff number submits the same direction, suburb, and day/time again, the
duplicate interest is skipped instead of counted twice.

Suggested matches are active entries with the same direction, the same suburb,
and at least one overlapping day/time. Add `--apply` to `suggest-matches` to
mark each suggested row as matched.

The `dedupe` command prints a cleanup plan for older duplicate interest rows.
Add `--apply` to perform the cleanup.

The `status-to-zero` command prints older blank or `pending` rows that should
be migrated to status `0`. Add `--apply` to perform the migration.

If macOS Python reports a certificate verification error, either run Python's
certificate installer or add `--insecure` to the command on your own machine.

Set a private `ADMIN_TOKEN` environment variable on the Render service before using the tool.
The admin endpoints return 404 until `ADMIN_TOKEN` is configured.

## Captured Fields

- Travelling to UWC or from UWC
- Starting suburb from an alphabetic list
- Travel schedule as exact day/time selections
- Student or staff number, used to group pool interest
- Popular pool counts from captured submissions
- Connection requests for organiser review

## Privacy Handling

Student or staff numbers are collected initially only to determine who falls
into common pools. Student or staff numbers, or UWC email addresses derived
from them, must not be shared with other people in a pool without explicit
consent at a later stage.

Before submission, users must tick an explicit consent checkbox confirming this
limited purpose. Providing a student or staff number is voluntary, but it is
required to join pools and prevent duplicate entries.

Public pages show aggregate pool counts only. Raw student or staff numbers are
available only through the token-protected admin tool. Route-interest records
should be deleted when they are no longer needed for the commute-club project.
Students can also remove a selected pool-interest record from the action panel
by entering their student or staff number, choosing the same direction, suburb,
and day/time, and using the remove button. This deletes only that selected
pool interest from the active database. The app does not keep extra copies
that continue storing a removed pool interest after that removal.

Submissions are kept with `status`, `connection_requests`, and
`connected_student_numbers` fields. New pool interests start with status
`0`, meaning the student has added themself to that pool. If the same pool
already has other active student or staff numbers, the new row starts with
status `1` and `connection_requests` records the existing numbers in that pool
for organiser review. No contact details are shared automatically. The
`consent-email` command prints yes/no consent links without showing those other
numbers to the requester. After the requester consents and the organiser sends
the target emails, `target-emails --apply` moves those numbers into
`connected_student_numbers` and marks the requester row as status `2`.
