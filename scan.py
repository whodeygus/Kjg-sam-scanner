#!/usr/bin/env python3
"""
KJG Daily SAM.gov Scan - plain deterministic script, no AI agent involved.

Pulls new federal contract opportunities from SAM.gov for Knox Jefferson
Group's registered NAICS codes, dedupes against Airtable, adds new records,
and emails a summary. Designed to run unattended on a schedule (GitHub
Actions cron) with no human confirmation step of any kind.
"""

import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NAICS_CODES = [
    "541611", "541512", "541618", "238910", "236220", "238160",
    "238320", "238210", "561210", "561730", "532289", "562111",
]

TIMEZONE = ZoneInfo("America/New_York")

AIRTABLE_BASE_ID = "app1y2Zcd4OkaR1Fh"
AIRTABLE_TABLE_ID = "tblCyhk2xPZUNtrAX"

SAM_API_KEY = os.environ["SAM_API_KEY"]
AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]
# Resend's shared sending domain - works without verifying your own domain.
FROM_EMAIL = os.environ.get("FROM_EMAIL", "KJG Scanner <onboarding@resend.dev>")

SAM_PAGE_SIZE = 25  # SAM.gov's public API appears to hard-cap pages at 25
SAM_MAX_RECORDS_PER_CODE = 500  # safety cap
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 20
PACE_SECONDS = 3  # delay between SAM.gov calls to avoid rate limiting

AIRTABLE_API = "https://api.airtable.com/v0"

FIELD_TITLE = "Title"
FIELD_STATUS = "Status"
FIELD_SOLICITATION = "Solicitation Number"
FIELD_AGENCY = "Agency"
FIELD_NAICS = "NAICS Code"
FIELD_DEADLINE = "Response Deadline"
FIELD_DATE_FOUND = "Date Found"
FIELD_PLACE = "Place of Performance"
FIELD_SETASIDE = "Set-Aside"
FIELD_URL = "Notice URL"


def now_eastern():
    return datetime.now(TIMEZONE)


# ---------------------------------------------------------------------------
# Airtable
# ---------------------------------------------------------------------------

def airtable_headers():
    return {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json",
    }


def fetch_existing_solicitation_numbers():
    """Paginate through the whole table and collect every Solicitation Number."""
    seen = set()
    url = f"{AIRTABLE_API}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
    params = {"fields[]": FIELD_SOLICITATION, "pageSize": 100}
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=airtable_headers(), params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for rec in data.get("records", []):
            sol = rec.get("fields", {}).get(FIELD_SOLICITATION)
            if sol:
                seen.add(sol)
        offset = data.get("offset")
        if not offset:
            break
    return seen


def create_airtable_records(records):
    """records: list of dicts already shaped as {fields: {...}}. Batches of 10."""
    url = f"{AIRTABLE_API}/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
    created = 0
    for i in range(0, len(records), 10):
        batch = records[i:i + 10]
        body = {"records": batch, "typecast": True}
        resp = requests.post(url, headers=airtable_headers(), json=body, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        created += len(resp.json().get("records", []))
    return created


# ---------------------------------------------------------------------------
# SAM.gov
# ---------------------------------------------------------------------------

def sam_search(ncode, posted_from, posted_to, offset):
    url = "https://api.sam.gov/opportunities/v2/search"
    params = {
        "api_key": SAM_API_KEY,
        "limit": SAM_PAGE_SIZE,
        "offset": offset,
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "ncode": ncode,
    }
    last_err = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except requests.RequestException as e:
            last_err = str(e)
        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise RuntimeError(f"SAM.gov request failed after {RETRY_ATTEMPTS} attempts: {last_err}")


def scan_naics_code(ncode, posted_from, posted_to, dedup_set):
    """Returns (status_line, list_of_new_opportunity_dicts)."""
    collected = []
    offset = 0
    total_records = None
    while True:
        time.sleep(PACE_SECONDS)
        try:
            data = sam_search(ncode, posted_from, posted_to, offset)
        except RuntimeError as e:
            if collected:
                # Partial success: keep what we got, note the truncation.
                return (f"PARTIAL - got {len(collected)} records before failing: {e}", collected)
            return (f"FAILED - {e}", [])

        total_records = data.get("totalRecords", 0)
        page = data.get("opportunitiesData", [])
        collected.extend(page)

        if not page:
            break
        if len(collected) >= total_records:
            break
        if len(collected) >= SAM_MAX_RECORDS_PER_CODE:
            break
        offset += len(page)

    new_opps = []
    for opp in collected:
        sol = opp.get("solicitationNumber")
        if not sol or sol in dedup_set:
            continue
        dedup_set.add(sol)
        new_opps.append(opp)

    note = ""
    if total_records and total_records > SAM_MAX_RECORDS_PER_CODE:
        note = f" (hit {SAM_MAX_RECORDS_PER_CODE}-record safety cap; {total_records} total reported)"
    status = f"OK - {len(new_opps)} new ({len(collected)} fetched of {total_records} total){note}"
    return (status, new_opps)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject, body):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [RECIPIENT_EMAIL],
            "subject": subject,
            "text": body,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()


def format_opportunity(opp):
    return (
        f"   {opp.get('title', '(no title)')}\n"
        f"   Solicitation: {opp.get('solicitationNumber', 'n/a')}\n"
        f"   Agency: {opp.get('fullParentPathName', 'n/a')}\n"
        f"   Deadline: {opp.get('responseDeadLine') or 'none listed'}\n"
        f"   Place: {opp.get('placeOfPerformance') or 'not specified'}\n"
        f"   Set-Aside: {opp.get('typeOfSetAsideDescription') or 'none listed'}\n"
        f"   {opp.get('uiLink', '')}\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    today = now_eastern().date()
    posted_from = (today - timedelta(days=3)).strftime("%m/%d/%Y")
    posted_to = today.strftime("%m/%d/%Y")
    date_found = today.isoformat()

    dedup_set = fetch_existing_solicitation_numbers()

    status_lines = []
    all_new = []  # list of (ncode, opp)
    for ncode in NAICS_CODES:
        status, new_opps = scan_naics_code(ncode, posted_from, posted_to, dedup_set)
        status_lines.append(f"{ncode}: {status}")
        for opp in new_opps:
            all_new.append((ncode, opp))

    records_to_create = []
    for ncode, opp in all_new:
        records_to_create.append({
            "fields": {
                FIELD_TITLE: opp.get("title") or "(no title)",
                FIELD_STATUS: "New",
                FIELD_SOLICITATION: opp.get("solicitationNumber"),
                FIELD_AGENCY: opp.get("fullParentPathName") or "",
                FIELD_NAICS: ncode,
                FIELD_DEADLINE: opp.get("responseDeadLine") or None,
                FIELD_DATE_FOUND: date_found,
                FIELD_PLACE: opp.get("placeOfPerformance") or "",
                FIELD_SETASIDE: opp.get("typeOfSetAsideDescription") or "",
                FIELD_URL: opp.get("uiLink") or "",
            }
        })

    created_count = 0
    airtable_error = None
    if records_to_create:
        try:
            created_count = create_airtable_records(records_to_create)
        except requests.RequestException as e:
            airtable_error = str(e)

    lines = []
    lines.append(f"KJG Daily SAM.gov Scan - {date_found} (America/New_York)")
    lines.append(f"Window checked: postedFrom {posted_from} to postedTo {posted_to}")
    lines.append("")
    lines.append("SCAN STATUS BY NAICS CODE")
    lines.extend(status_lines)
    lines.append("")
    lines.append(f"TOTAL NEW OPPORTUNITIES FOUND: {len(all_new)}")
    if airtable_error:
        lines.append(f"WARNING: found {len(all_new)} new opportunities but failed to write them to Airtable: {airtable_error}")
    else:
        lines.append(f"TOTAL NEW OPPORTUNITIES ADDED TO AIRTABLE: {created_count}")
    lines.append("")

    if all_new:
        by_code = {}
        for ncode, opp in all_new:
            by_code.setdefault(ncode, []).append(opp)
        for ncode, opps in by_code.items():
            lines.append("=" * 50)
            lines.append(f"NAICS {ncode} ({len(opps)} new)")
            lines.append("=" * 50)
            for i, opp in enumerate(opps, 1):
                lines.append(f"{i}. {format_opportunity(opp)}")
    else:
        lines.append("No new opportunities found in this window.")

    lines.append("")
    lines.append("This scan ran as a plain scheduled script (GitHub Actions) - no AI agent")
    lines.append("involved at runtime, so there is no confirmation-gate failure mode.")

    body = "\n".join(lines)
    subject = f"KJG Daily SAM.gov Scan - {date_found}"
    send_email(subject, body)
    print(body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            send_email(
                f"KJG Daily SAM.gov Scan - CRASHED - {now_eastern().date().isoformat()}",
                f"The scan script crashed before completing:\n\n{tb}",
            )
        except Exception:
            pass
        sys.exit(1)
