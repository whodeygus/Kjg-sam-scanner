#!/usr/bin/env python3
"""
KJG Scan Watchdog - plain deterministic script, no AI agent involved.

Checks whether today's KJG Daily SAM.gov Scan actually completed
successfully. GitHub Actions can occasionally skip a scheduled cron
trigger entirely with no error and no notification - this catches
that silent-failure case, since a missed day is not acceptable for
this business.
"""

import os
import re
import sys
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests

TIMEZONE = ZoneInfo("America/New_York")
SCAN_WORKFLOW_FILE = "scan.yml"
REQUEST_TIMEOUT = 30


def _clean(value):
    """See scan.py's _clean() - strips non-printable-ASCII characters
    anywhere in the string, guarding against invisible characters
    smuggled in via copy-paste into GitHub's secret UI."""
    return re.sub(r"[^\x21-\x7e]", "", value)


def _env(name):
    return _clean(os.environ[name])


GITHUB_TOKEN = _env("GITHUB_TOKEN")
GITHUB_REPOSITORY = _env("GITHUB_REPOSITORY")
GMAIL_ADDRESS = _env("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = _env("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = _env("RECIPIENT_EMAIL")


def now_eastern():
    return datetime.now(TIMEZONE)


def send_email(subject, body):
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [RECIPIENT_EMAIL], msg.as_string())


def fetch_recent_runs():
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/{SCAN_WORKFLOW_FILE}/runs"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = requests.get(url, headers=headers, params={"per_page": 15}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("workflow_runs", [])


def main():
    today = now_eastern().date()
    runs = fetch_recent_runs()

    todays_runs = []
    for run in runs:
        created_at = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if created_at.astimezone(TIMEZONE).date() == today:
            todays_runs.append(run)

    succeeded = [r for r in todays_runs if r.get("conclusion") == "success"]
    if succeeded:
        print(f"OK - found {len(succeeded)} successful scan run(s) today ({today.isoformat()}).")
        return

    lines = [
        f"WATCHDOG ALERT: No successful KJG Daily SAM.gov Scan run found for "
        f"{today.isoformat()} (America/New_York).",
        "",
    ]
    if not todays_runs:
        lines.append("No workflow runs of any kind were recorded for today at all.")
        lines.append("This likely means GitHub Actions silently skipped the scheduled trigger.")
        lines.append("ACTION NEEDED: go to the Actions tab and manually run 'KJG Daily SAM.gov Scan' now.")
    else:
        lines.append(f"{len(todays_runs)} run(s) were recorded today, but none succeeded:")
        for r in todays_runs:
            lines.append(
                f"  - run #{r.get('run_number')}: event={r.get('event')}, "
                f"status={r.get('status')}, conclusion={r.get('conclusion')}, "
                f"url={r.get('html_url')}"
            )
        lines.append("")
        lines.append("ACTION NEEDED: check the run logs above and re-run manually if needed.")

    body = "\n".join(lines)
    send_email(f"KJG Scan Watchdog ALERT - {today.isoformat()} - scan did not complete", body)
    print(body)
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        try:
            send_email(
                f"KJG Scan Watchdog CRASHED - {now_eastern().date().isoformat()}",
                f"The watchdog script itself crashed before it could check anything:\n\n{e}",
            )
        except Exception:
            pass
        print(f"Watchdog crashed: {e}", file=sys.stderr)
        sys.exit(1)
