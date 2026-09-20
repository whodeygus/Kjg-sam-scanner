# KJG Daily SAM.gov Scan

Plain scheduled script (no AI agent at runtime) that pulls new federal
contract opportunities from SAM.gov for Knox Jefferson Group's registered
NAICS codes, dedupes against the KJG Opportunity Tracker Airtable base, adds
new records, and emails a summary. Runs daily via GitHub Actions.

A companion watchdog workflow runs an hour later each day and checks
GitHub's own record of whether the scan actually ran and succeeded,
alerting by email if it didn't (see **Watchdog** below).

## One-time setup

Add these as repo secrets: **Settings -> Secrets and variables -> Actions ->
New repository secret**.

| Secret name | Value |
|---|---|
| `SAM_API_KEY` | Your SAM.gov API key (same one the Claude routine used) |
| `AIRTABLE_TOKEN` | An Airtable Personal Access Token (see below) |
| `GMAIL_ADDRESS` | The Gmail account the scan/watchdog send from, e.g. `gustin.puckett@gmail.com` |
| `GMAIL_APP_PASSWORD` | A Gmail App Password for that account (see below) |
| `RECIPIENT_EMAIL` | Where summaries and alerts should be sent, e.g. `gustin.puckett@knoxjefferson.com` |

The watchdog needs no extra secrets - it reuses the Gmail secrets above, and
its GitHub API access comes from the `GITHUB_TOKEN` that Actions provides
automatically to every workflow run.

### Getting an Airtable Personal Access Token

1. Go to https://airtable.com/create/tokens
2. Create a new token, e.g. named "KJG Scanner"
3. Scopes: `data.records:read` and `data.records:write`
4. Access: add the specific base "KJG Opportunity Tracker" (base ID
   `app1y2Zcd4OkaR1Fh`) - no need to grant access to any other base
5. Copy the token (starts with `pat...`) into the `AIRTABLE_TOKEN` secret

### Getting a Gmail App Password

1. The Gmail account must have 2-Step Verification turned on
   (myaccount.google.com/security). This only works reliably on a personal
   Gmail account - Google Workspace accounts can have App Passwords disabled
   by admin policy with no user-facing toggle.
2. Go to https://myaccount.google.com/apppasswords
3. Create an app password (any name, e.g. "KJG Scanner")
4. Copy the 16-character password into the `GMAIL_APP_PASSWORD` secret, and
   put the Gmail address itself in `GMAIL_ADDRESS`

## Testing it

Once secrets are added, go to the **Actions** tab -> **KJG Daily SAM.gov
Scan** -> **Run workflow** to fire it manually and confirm it works before
relying on the schedule. Do the same for **KJG Scan Watchdog** to confirm it
can reach the GitHub API and reports "OK" when a successful scan exists for
today.

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

## Why this exists

This replaces an earlier version of the same scan that ran as a Claude
Code "Routine" (a scheduled AI agent session). That approach worked most
days but intermittently got blocked by a platform-level confirmation gate
that unattended AI agent sessions can hit on certain tool calls - since
nobody is present to click "approve," the run would fail. This script has
no AI agent in its runtime path at all, so that failure mode does not
apply to it. The watchdog is built the same way, for the same reason.
