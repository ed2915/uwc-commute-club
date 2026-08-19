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
python3 tools/render_submissions.py removals
python3 tools/render_submissions.py suggest-matches
python3 tools/render_submissions.py dedupe
python3 tools/render_submissions.py status-to-zero
python3 tools/render_submissions.py set sub_abc123 --status matched
python3 tools/render_submissions.py consent-email sub_abc123
python3 tools/render_submissions.py connect sub_abc123 --add 7654321 2345678
python3 tools/render_submissions.py delete sub_abc123
```

New submissions are stored as one interest row per selected pool. If a student
number submits the same direction, suburb, and day/time again, the
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

## Encrypted Daily Backups

`tools/backup_render_submissions.py` fetches the live submission rows, rebuilds
the CSV in memory, and encrypts it with AES-256-GCM before writing anything to
disk. The dedicated 256-bit encryption key is held in macOS Keychain under
`za.ac.uwc.commute-club.backup-key`.

The installed LaunchAgent runs daily maintenance at 20:00 local time and at
login. It first creates at most one encrypted backup per day, retains backups
for 30 days, and writes them to the protected directory
`/Users/ed/Ideas/carpooler/backups`. The directory has mode `700` and backup files have
mode `600`. Only after a backup succeeds, it removes unanswered pool requests
that reached the three-reminder limit at least three days earlier. The matching
historical connection-request row is removed as part of the same cleanup.

Useful commands, using the Python environment that contains `cryptography`:

```sh
source ~/myenv/bin/activate

# Create today's backup manually. A duplicate same-day run is skipped.
python3 tools/backup_render_submissions.py --insecure backup

# Preview unanswered requests that are old enough for automatic removal.
UWC_ADMIN_TOKEN="$(cat ~/.config/uwc-commute-club/admin-token)" \
  python3 tools/render_submissions.py --insecure expire-unanswered-consent

# Run the same backup-first maintenance sequence used by the schedule.
python3 tools/backup_render_submissions.py --insecure daily-maintenance

# List and cryptographically verify backups.
python3 tools/backup_render_submissions.py list
python3 tools/backup_render_submissions.py verify

# Recover one backup to a protected plaintext CSV.
python3 tools/backup_render_submissions.py decrypt \
  backups/submissions-YYYYMMDDTHHMMSS-SAST.csv.aes \
  --output ~/recovered-submissions.csv

# Reinstall or update the daily 20:00 maintenance schedule.
python3 tools/backup_render_submissions.py --insecure install-schedule
```

The scheduled-job log is `/Users/ed/Ideas/carpooler/logs/backup.log`. The
LaunchAgent definition is
`~/Library/LaunchAgents/za.ac.uwc.commute-club.backup.plist`.

Do not delete or replace the Keychain encryption key while retained backups
still need to be recoverable. A decrypted recovery CSV contains personal
information and should be deleted as soon as recovery work is complete.

## Participation Chart

Activate the Python environment containing Matplotlib, set `UWC_ADMIN_TOKEN`,
and generate an aggregate day-by-day chart from the live Render database:

```sh
source ~/myenv/bin/activate
python3 tools/plot_participation.py --insecure
```

The default output is `reports/participation_over_time.png`. Limit the displayed
period or choose another output format with:

```sh
python3 tools/plot_participation.py --insecure \
  --start 2026-08-01 \
  --end 2026-08-31 \
  --output reports/august_participation.pdf
```

The report contains aggregate counts only. It does not save student
numbers. Because removed pool interests are deleted from the active database,
the script cannot reconstruct participation that was subsequently removed.

## Captured Fields

- Travelling to UWC or from UWC
- Starting suburb from an alphabetic list
- Travel schedule as exact day/time selections
- Valid 7-digit student number or 6-digit staff number, used to group pool interest
- UWC staff email address and explicit sharing consent for staff submissions
- Popular pool counts from captured submissions
- Connection requests for organiser review

## Privacy Handling

Student and staff numbers are collected initially to determine who falls into
common pools. A student's derived UWC email address must not be shared with
other people in a pool without explicit consent at a later stage. Because a
staff email address cannot be derived from a six-digit staff number, the staff
member supplies it in a separate dialog and explicitly consents to its storage
and sharing when needed to facilitate a pool connection.

Before submission, users must tick an explicit consent checkbox confirming this
limited purpose. Staff must also confirm the separate email-sharing consent.
Providing a UWC number is voluntary, but it is required to join pools and
prevent duplicate entries.

Public pages show aggregate pool counts only. Raw UWC numbers and staff email
addresses are available only through the token-protected admin tool. Route-interest records
should be deleted when they are no longer needed for the commute-club project.
Students and staff can also remove a selected pool-interest record from the action panel
by entering their UWC number, choosing the same direction, suburb,
and day/time, and using the remove button. This deletes only that selected
pool interest from the active database. After a successful removal, an optional
reason is written to `removal_events.csv` with the pool details and timestamp.
The removal event does not contain a UWC number or email address and cannot be
linked back to the person through that file. The admin `removals` command shows
the anonymous total and reason breakdown. Encrypted disaster-recovery backups
retain deleted records for up to 30 days, and Render snapshots may retain them
for Render's snapshot-retention period. Recovery copies are used only to
restore the service after data loss; they are not used for matching or contact
sharing.

Submissions are kept with `status`, `connection_requests`, and
`connected_student_numbers` fields. New pool interests start with status
`0`, meaning the person has added themself to that pool. If the same pool
already has other active UWC numbers, the new row starts with
status `1` and `connection_requests` records the existing numbers in that pool
for organiser review. Student contact details are not shared automatically.
Staff who supply an email address consent at submission to its use and sharing
when needed for a relevant pool connection. The
`consent-email` command prints yes/no consent links without showing those other
numbers to the requester. After the requester consents and the organiser sends
the target emails, `target-emails --apply` moves those numbers into
`connected_student_numbers` and marks the requester row as status `2`.

When `review-actions` generates a consent email, it also writes the complete
message to `consent_email.txt` in the project directory. After the organiser
confirms that the email was sent, the script records `consent_email_sent_at`.
That original timestamp is retained. While a response remains outstanding,
`review-actions` offers a reminder every three days on a schedule anchored to
the original send time, up to a maximum of three reminders. Confirmed reminders
update `consent_email_last_sent_at` and `consent_reminder_count` so the same
reminder is not offered twice and no further reminder is offered after the
third.
Another pending submission belonging to the same person remains deferred. The
local text file is removed after sending is confirmed so it does not become an
extra retained copy. An intentional manual resend requires
`consent-email ID --force`.

### Codex Gmail plugin workflow

Routine email handling uses the Codex Gmail plugin connected only to
`uwccommuteclub@uwc.ac.za`. The Render administration script remains the source
of truth for pending actions and database reconciliation.

Ask Codex to "Review UWC Commute Club activity and prepare the necessary Gmail
drafts." Codex checks Render, creates only the missing drafts, and reports what
is awaiting a response. Drafts are never sent without an explicit instruction.
After a draft is sent, Codex updates the matching Render row so that reminders,
connections, and rejected requests are handled correctly without duplicate
emails.
