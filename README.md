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

## Captured Fields

- Travelling to UWC or from UWC
- Starting suburb from an alphabetic list
- Travel schedule as exact day/time selections
- Nickname only, with no personal details
- Popular route and time counts from captured submissions

Submissions are kept with a `status` field. Future matching can mark records as `matched` with a `matched_group_id`, which keeps an audit trail while preventing already-connected people from being matched again.
