#!/usr/bin/env python3
"""
KJG Daily SAM.gov Scan - plain deterministic script, no AI agent involved.

Pulls new federal contract opportunities from SAM.gov for Knox Jefferson
Group's registered NAICS codes, dedupes against a Google Sheet, adds new
rows, and emails a summary. Designed to run unattended on a schedule
(GitHub Actions cron) with no human confirmation step of any kind.
"""

import json
import os
import re
import sys
import time
import smtplib
import traceback
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NAICS_CODES = [
    "541611", "541512", "541618", "238910", "236220", "238160",
    "238320", "238210", "561210", "561730", "532289", "562111",
]

TIMEZONE = ZoneInfo("America/New_York")

SHEET_TAB_NAME = "Opportunities"

# Default column layout, used only to create the tab's header row the very
# first time the sheet is set up. Once the sheet exists, the live header row
# is read at runtime (see get_sheet_header()) and used to place every field
# in whichever column it's actually under - so reordering columns by hand in
# the Sheets UI (Gus does this to suit his own reading order) never breaks
# the write path, unlike a hardcoded column order would.
DEFAULT_HEADER = [
    "Title", "Status", "Link", "Date Found", "Response Deadline",
    "Place of Performance", "Agency", "NAICS Code", "Set-Aside",
    "Solicitation Number", "Subcontractor", "Sub Contact Info", "Notes",
    "Call Script", "Submission", "Sub Contractor",
]
COL_SOLICITATION = "Solicitation Number"


def _clean(value):
    """Strip whitespace and any non-printable/control/zero-width characters.

    Mobile copy-paste (and pasting into GitHub's secret UI) can smuggle in
    invisible characters - zero-width spaces, BOM markers, stray CR/LF in
    the middle of the string, etc. - that plain .strip() won't catch since
    they aren't at the very start/end or aren't classic whitespace. None of
    our credentials (API keys, tokens, email addresses) legitimately
    contain any whitespace or control characters, so it's safe to strip
    every such character wherever it appears, not just at the edges.
    """
    # Keep only printable ASCII, excluding whitespace entirely.
    return re.sub(r"[^\x21-\x7e]", "", value)


def _env(name):
    return _clean(os.environ[name])


SAM_API_KEY = _env("SAM_API_KEY")
GMAIL_ADDRESS = _env("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = _env("GMAIL_APP_PASSWORD")  # also strips spaces Google displays it with
RECIPIENT_EMAIL = _env("RECIPIENT_EMAIL")
GOOGLE_SHEET_ID = _env("GOOGLE_SHEET_ID")
# The service account key JSON is a multi-line/whitespace-containing blob,
# so it can't go through the same character-stripping _clean() as the
# single-token secrets above - just read it raw.
GOOGLE_SHEETS_CREDENTIALS_JSON = os.environ["GOOGLE_SHEETS_CREDENTIALS_JSON"]

SAM_PAGE_SIZE = 25  # SAM.gov's public API appears to hard-cap pages at 25
SAM_MAX_RECORDS_PER_CODE = 500  # safety cap
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 20
PACE_SECONDS = 3  # delay between SAM.gov calls to avoid rate limiting

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def now_eastern():
    return datetime.now(TIMEZONE)


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def open_sheet():
    """Authenticate with the service account and return the worksheet,
    creating the tab with a header row if this is the very first run."""
    info = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(SHEET_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=SHEET_TAB_NAME, rows=1000, cols=len(DEFAULT_HEADER)
        )
        worksheet.append_row(DEFAULT_HEADER, value_input_option="RAW")

    return worksheet


def get_sheet_header(worksheet):
    """The sheet's actual current column order - read fresh every run so a
    manual column reorder in the Sheets UI is picked up automatically."""
    header = worksheet.row_values(1)
    return header if header else DEFAULT_HEADER


def fetch_existing_solicitation_numbers(worksheet, header):
    """Read the whole Solicitation Number column to build the dedup set."""
    try:
        col_idx = header.index(COL_SOLICITATION)
    except ValueError:
        # Column missing from the header - treat as empty dedup set rather
        # than crash; new rows still get written under whatever header exists.
        return set()
    values = worksheet.get_values()  # includes header row
    seen = set()
    for row in values[1:]:
        if col_idx < len(row) and row[col_idx]:
            seen.add(row[col_idx])
    return seen


def create_sheet_rows(worksheet, header, rows):
    """rows: list of dicts keyed by column name. Appended in the sheet's own
    current column order, whatever that is, not a fixed/assumed order."""
    values = [[row.get(col, "") for col in header] for row in rows]
    worksheet.append_rows(values, value_input_option="RAW")
    return len(values)


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
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [RECIPIENT_EMAIL], msg.as_string())


def _is_real_value(value):
    """SAM.gov sometimes fills a missing city/state/zip with the literal
    placeholder "0" instead of leaving it out - treat that as absent."""
    return bool(value) and value.strip() != "0"


def format_place(place):
    """SAM.gov's placeOfPerformance is sometimes a nested dict (city/state/
    zip/country sub-objects), sometimes a plain string, sometimes missing."""
    if not place:
        return "not specified"
    if isinstance(place, str):
        return place

    parts = []
    street = place.get("streetAddress")
    if _is_real_value(street):
        parts.append(street)

    city = place.get("city")
    city_name = city.get("name") if isinstance(city, dict) else city
    if _is_real_value(city_name):
        parts.append(city_name)

    state = place.get("state")
    state_name = state.get("name") if isinstance(state, dict) else state
    state_code = state.get("code") if isinstance(state, dict) else None
    if _is_real_value(state_code):
        parts.append(state_code)
    elif _is_real_value(state_name):
        parts.append(state_name)

    zip_code = place.get("zip")
    if _is_real_value(zip_code):
        parts.append(zip_code)

    country = place.get("country")
    country_name = country.get("name") if isinstance(country, dict) else country
    country_code = country.get("code") if isinstance(country, dict) else None
    if _is_real_value(country_name) and country_name.upper() not in ("UNITED STATES", "USA"):
        parts.append(country_name)
    elif _is_real_value(country_code) and country_code.upper() not in ("USA", "US"):
        parts.append(country_code)

    return ", ".join(parts) if parts else "not specified"


def format_opportunity(opp):
    return (
        f"   {opp.get('title', '(no title)')}\n"
        f"   Solicitation: {opp.get('solicitationNumber', 'n/a')}\n"
        f"   Agency: {opp.get('fullParentPathName', 'n/a')}\n"
        f"   Deadline: {opp.get('responseDeadLine') or 'none listed'}\n"
        f"   Place: {format_place(opp.get('placeOfPerformance'))}\n"
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

    worksheet = open_sheet()
    header = get_sheet_header(worksheet)
    dedup_set = fetch_existing_solicitation_numbers(worksheet, header)

    status_lines = []
    all_new = []  # list of (ncode, opp)
    for ncode in NAICS_CODES:
        status, new_opps = scan_naics_code(ncode, posted_from, posted_to, dedup_set)
        status_lines.append(f"{ncode}: {status}")
        for opp in new_opps:
            all_new.append((ncode, opp))

    rows_to_create = []
    for ncode, opp in all_new:
        rows_to_create.append({
            "Title": opp.get("title") or "(no title)",
            "Status": "New",
            "Solicitation Number": opp.get("solicitationNumber") or "",
            "Agency": opp.get("fullParentPathName") or "",
            "NAICS Code": ncode,
            "Response Deadline": opp.get("responseDeadLine") or "",
            "Date Found": date_found,
            "Place of Performance": format_place(opp.get("placeOfPerformance")),
            "Set-Aside": opp.get("typeOfSetAsideDescription") or "",
            "Link": opp.get("uiLink") or "",
        })

    created_count = 0
    sheet_error = None
    if rows_to_create:
        try:
            created_count = create_sheet_rows(worksheet, header, rows_to_create)
        except Exception as e:
            sheet_error = str(e)

    lines = []
    lines.append(f"KJG Daily SAM.gov Scan - {date_found} (America/New_York)")
    lines.append(f"Window checked: postedFrom {posted_from} to postedTo {posted_to}")
    lines.append("")
    lines.append("SCAN STATUS BY NAICS CODE")
    lines.extend(status_lines)
    lines.append("")
    lines.append(f"TOTAL NEW OPPORTUNITIES FOUND: {len(all_new)}")
    if sheet_error:
        lines.append(f"WARNING: found {len(all_new)} new opportunities but failed to write them to the Google Sheet: {sheet_error}")
    else:
        lines.append(f"TOTAL NEW OPPORTUNITIES ADDED TO SHEET: {created_count}")
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
