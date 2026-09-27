# KJG Daily SAM.gov Scan

Plain scheduled script (no AI agent at runtime) that pulls new federal
contract opportunities from SAM.gov for Knox Jefferson Group's registered
NAICS codes, dedupes against the KJG Opportunity Tracker Google Sheet, adds
new rows, and emails a summary. Runs daily via GitHub Actions.

A companion watchdog workflow runs an hour later each day and checks
GitHub's own record of whether the scan actually ran and succeeded,
alerting by email if it didn't (see **Watchdog** below).

> **Migrated from Airtable (Sept 2026):** this used to write to an Airtable
> base, which hit Airtable's storage cap. It now writes to a Google Sheet
> instead, which has no realistic storage limit for this use. See
> **One-time setup** below, including the **one-time backfill** step that
> loads everything that used to be in Airtable into the new sheet.

## One-time setup

### 1. Create the Google Sheet

1. Go to https://sheets.google.com and create a new blank spreadsheet.
2. Name it whatever you like, e.g. "KJG Opportunity Tracker".
3. Copy its Sheet ID out of the URL - the long string between `/d/` and
   `/edit`, e.g. for
   `https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit`
   the ID is `1AbCdEfGhIjKlMnOpQrStUvWxYz`.
4. Leave it empty - the backfill step below fills in the header row and all
   historical data for you. Don't add a tab named "Opportunities" yourself;
   the scripts create it.

### 2. Create a Google Cloud service account (so the script can write to the sheet)

This is the one genuinely new/unfamiliar step. A "service account" is a
robot Google account that only your script uses - it's not your personal
Gmail login.

1. Go to https://console.cloud.google.com/ and create a new project (or use
   an existing one) - top-left project dropdown -> "New Project". Any name
   is fine, e.g. "kjg-scanner".
2. With that project selected, go to **APIs & Services -> Library**, search
   for "Google Sheets API", and click **Enable**.
3. Go to **APIs & Services -> Credentials -> Create Credentials -> Service
   account**. Give it any name, e.g. "kjg-sheets-writer". Click through the
   remaining steps with defaults (no roles needed, no user access needed) -
   **Done**.
4. Click into the service account you just created -> **Keys** tab ->
   **Add Key -> Create new key -> JSON**. This downloads a `.json` file to
   your computer - this is the credential the script uses. Keep it private;
   treat it like a password.
5. Open that downloaded JSON file in a text editor and copy the value of
   its `client_email` field - it looks like
   `kjg-sheets-writer@kjg-scanner-123456.iam.gserviceaccount.com`.
6. Back in your Google Sheet, click **Share**, paste that service-account
   email in, give it **Editor** access, and share (uncheck "Notify people" -
   it's a robot, it won't read the email).

### 3. Add repo secrets

**Settings -> Secrets and variables -> Actions -> New repository secret**.
`AIRTABLE_TOKEN` is gone - these two replace it:

| Secret name | Value |
|---|---|
| `SAM_API_KEY` | Your SAM.gov API key (unchanged) |
| `GOOGLE_SHEET_ID` | The Sheet ID from step 1 |
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | The **entire contents** of the JSON key file from step 2.4 - open the file, select all, copy, paste the whole thing as the secret value |
| `GMAIL_ADDRESS` | The Gmail account the scan/watchdog send from, e.g. `gustin.puckett@gmail.com` (unchanged) |
| `GMAIL_APP_PASSWORD` | A Gmail App Password for that account (unchanged, see below) |
| `RECIPIENT_EMAIL` | Where summaries and alerts should be sent, e.g. `gustin.puckett@knoxjefferson.com` (unchanged) |

The watchdog needs no extra secrets - it reuses the Gmail secrets above, and
its GitHub API access comes from the `GITHUB_TOKEN` that Actions provides
automatically to every workflow run.

### 4. Run the one-time backfill

Go to **Actions** tab -> **KJG Sheet Backfill (one-time)** -> **Run
workflow**. This loads all ~1,105 historical records from
`historical_data.csv` (a snapshot taken from the old Airtable base) into
your new sheet in one shot, in the same column layout Airtable used. It
refuses to run a second time once the sheet has data, so it's safe if you
click it twice by accident.

Once it succeeds, you can delete `backfill.py`, `historical_data.csv`, and
`.github/workflows/backfill.yml` from the repo - they've done their job and
the daily scan doesn't use them.

### Getting a Gmail App Password

(Unchanged from before.)

1. The Gmail account must have 2-Step Verification turned on
   (myaccount.google.com/security). This only works reliably on a personal
   Gmail account - Google Workspace accounts can have App Passwords disabled
   by admin policy with no user-facing toggle.
2. Go to https://myaccount.google.com/apppasswords
3. Create an app password (any name, e.g. "KJG Scanner")
4. Copy the 16-character password into the `GMAIL_APP_PASSWORD` secret, and
   put the Gmail address itself in `GMAIL_ADDRESS`

## Testing it

Once secrets are added and the backfill has run, go to **Actions** ->
**KJG Daily SAM.gov Scan** -> **Run workflow** to fire it manually and
confirm it works before relying on the schedule. Do the same for **KJG Scan
Watchdog** to confirm it can reach the GitHub API and reports "OK" when a
successful scan exists for today.

## Schedule

- **Scan**: daily at 10:05 UTC (6:05am ET during daylight saving time).
- **Watchdog**: daily at 11:05 UTC, one hour after the scan.

Edit the cron lines in `.github/workflows/scan.yml` and `watchdog.yml` if you
want different times, or adjust them once EST resumes in November if you
want them pinned to a fixed local time.

## Watchdog

`watchdog.py` calls the GitHub Actions API to check whether a run of the
scan workflow completed successfully today. This exists because GitHub
Actions can occasionally skip a scheduled cron trigger entirely - silently,
with no error - which would otherwise mean a missed day with no warning.

- If a successful scan run is found for today, the watchdog exits quietly
  (no email, so you're not getting a second email every day for good news).
- If no run exists for today at all, or every run today failed, it sends an
  alert email telling you to go run the scan manually.
- If the watchdog itself crashes (e.g. the GitHub API is unreachable), it
  also emails you about that.

## Sheet layout

The "Opportunities" tab has these columns, in this order:

`Title, Status, Solicitation Number, Agency, NAICS Code, Notice Type,
Response Deadline, Date Found, Place of Performance, Set-Aside,
Subcontractor, Sub Contact Info, Link, Notes, Call Script, Submission,
Sub Contractor`

The daily scan only ever fills in the first ten (through `Link`) plus
`Status` (always set to "New" for a freshly-found opportunity). The rest -
`Subcontractor`, `Notes`, `Call Script`, etc. - are yours to fill in by hand
as you work each pursuit, exactly like before.

## Why this exists

This replaces an earlier version of the same scan that ran as a Claude
Code "Routine" (a scheduled AI agent session). That approach worked most
days but intermittently got blocked by a platform-level confirmation gate
that unattended AI agent sessions can hit on certain tool calls - since
nobody is present to click "approve," the run would fail. This script has
no AI agent in its runtime path at all, so that failure mode does not
apply to it. The watchdog is built the same way, for the same reason.
