# UWC Commute Club

A small web-first pilot for grouping University of the Western Cape commuters by area, day, and travel time.

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
python3 tools/render_submissions.py suggest-matches
python3 tools/render_submissions.py dedupe
python3 tools/render_submissions.py set sub_abc123 --status matched --matched-group-id group_1
python3 tools/render_submissions.py delete sub_abc123
```

New submissions are stored as one interest row per selected day/time cell. If a
nickname submits the same direction, suburb, and day/time again, the duplicate
interest is skipped instead of counted twice.

Suggested matches are pending entries with the same direction, the same suburb,
and at least one overlapping day/time. Add `--apply` to `suggest-matches` to
mark each suggested group as matched with a generated group id.

The `dedupe` command prints a cleanup plan for older duplicate interest rows.
Add `--apply` to perform the cleanup.

If macOS Python reports a certificate verification error, either run Python's
certificate installer or add `--insecure` to the command on your own machine.

Set a private `ADMIN_TOKEN` environment variable on the Render service before using the tool.
The admin endpoints return 404 until `ADMIN_TOKEN` is configured.

## Captured Fields

- Travelling to UWC or from UWC
- Starting suburb from an alphabetic list
- Travel schedule as exact day/time selections
- Nickname only, with no personal details
- Popular route and time counts from captured submissions

Submissions are kept with a `status` field. Future matching can mark records as `matched` with a `matched_group_id`, which keeps an audit trail while preventing already-connected people from being matched again.
