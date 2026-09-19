# KJG Daily SAM.gov Scan

Plain scheduled script (no AI agent at runtime) that pulls new federal
contract opportunities from SAM.gov for Knox Jefferson Group's registered
NAICS codes, dedupes against the KJG Opportunity Tracker Airtable base, adds
new records, and emails a summary. Runs daily via GitHub Actions.

## One-time setup

Add these as repo secrets: **Settings -> Secrets and variables -> Actions ->
New repository secret**.

| Secret name | Value |
|---|---|
| `SAM_API_KEY` | Your SAM.gov API key (same one the Claude routine used) |
| `AIRTABLE_TOKEN` | An Airtable Personal Access Token (see below) |
| `RESEND_API_KEY` | A Resend API key (see below) |
| `RECIPIENT_EMAIL` | Where the summary should be sent, e.g. `gustin.puckett@knoxjefferson.com` |

### Getting an Airtable Personal Access Token

1. Go to https://airtable.com/create/tokens
2. Create a new token, e.g. named "KJG Scanner"
3. Scopes: `data.records:read` and `data.records:write`
4. Access: add the specific base "KJG Opportunity Tracker" (base ID
   `app1y2Zcd4OkaR1Fh`) - no need to grant access to any other base
5. Copy the token (starts with `pat...`) into the `AIRTABLE_TOKEN` secret

### Getting a Resend API key

1. Go to https://resend.com and sign up (works fine on a phone, no domain
   verification needed to send yourself alerts from their shared sending
   domain)
2. Go to **API Keys** -> **Create API Key**
3. Copy the key (starts with `re_`) into the `RESEND_API_KEY` secret

## Testing it

Once secrets are added, go to the **Actions** tab -> **KJG Daily SAM.gov
Scan** -> **Run workflow** to fire it manually and confirm it works before
relying on the schedule.

## Schedule

Runs daily at 10:05 UTC (6:05am ET during daylight saving time). Edit the
cron line in `.github/workflows/scan.yml` if you want a different time, or
adjust it once EST resumes in November if you want it pinned to a fixed
local time.

## Why this exists

This replaces an earlier version of the same scan that ran as a Claude
Code "Routine" (a scheduled AI agent session). That approach worked most
days but intermittently got blocked by a platform-level confirmation gate
that unattended AI agent sessions can hit on certain tool calls - since
nobody is present to click "approve," the run would fail. This script has
no AI agent in its runtime path at all, so that failure mode does not
apply to it.
