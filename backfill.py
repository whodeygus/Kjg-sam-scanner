#!/usr/bin/env python3
"""
One-time backfill: loads historical_data.csv (everything exported from the
old Airtable base) into the Google Sheet, then this script and its workflow
can be deleted.

Safe to re-run: if the "Opportunities" tab already has more than just a
header row, it refuses to run again rather than duplicating everything.
Run manually from the Actions tab -> "KJG Sheet Backfill (one-time)" ->
"Run workflow". Do this once, after creating the Google Sheet and secrets
(see README.md), and before the daily scan's first scheduled run.
"""

import csv
import json
import os
import re
import sys

import gspread
from google.oauth2.service_account import Credentials

SHEET_TAB_NAME = "Opportunities"
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historical_data.csv")
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _clean(value):
    return re.sub(r"[^\x21-\x7e]", "", value)


GOOGLE_SHEET_ID = _clean(os.environ["GOOGLE_SHEET_ID"])
GOOGLE_SHEETS_CREDENTIALS_JSON = os.environ["GOOGLE_SHEETS_CREDENTIALS_JSON"]


def main():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        print("historical_data.csv is empty - nothing to backfill.")
        return

    header, data_rows = rows[0], rows[1:]
    print(f"Loaded {len(data_rows)} historical rows from {CSV_PATH}.")

    info = json.loads(GOOGLE_SHEETS_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(SHEET_TAB_NAME)
        existing = worksheet.get_values()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=SHEET_TAB_NAME, rows=max(1000, len(data_rows) + 10), cols=len(header)
        )
        existing = []

    if len(existing) > 1:
        print(
            f"Refusing to backfill: '{SHEET_TAB_NAME}' already has "
            f"{len(existing) - 1} data row(s). This backfill is meant to run "
            f"exactly once against an empty sheet. If you really want to "
            f"re-run it, clear the tab first."
        )
        sys.exit(1)

    if not existing:
        worksheet.append_row(header, value_input_option="RAW")

    # append_rows in batches to stay well under any single-request size limit
    batch_size = 200
    total = 0
    for i in range(0, len(data_rows), batch_size):
        batch = data_rows[i:i + batch_size]
        worksheet.append_rows(batch, value_input_option="RAW")
        total += len(batch)
        print(f"  ...wrote {total}/{len(data_rows)} rows")

    print(f"Backfill complete: {total} historical rows added to '{SHEET_TAB_NAME}'.")
    print("You can now delete backfill.py, historical_data.csv, and "
          ".github/workflows/backfill.yml from the repo - they've done their job.")


if __name__ == "__main__":
    main()
