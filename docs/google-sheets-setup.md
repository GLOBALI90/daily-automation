# Google Sheets database setup (one-time)

The social collector uses the Google Sheet as its durable source of truth. It reads `Leads` and `Personal Profiles` before calling social providers, then upserts results back to those tabs. Runner-local CSVs are only temporary compatibility caches and are not committed or uploaded.

Spreadsheet: https://docs.google.com/spreadsheets/d/1vxbWKz0eIlXeGUIkQllxCWBf-9U8_4Td3p1YjCUWI70/edit

## 1. Create the authenticated Apps Script bridge

1. Open https://script.google.com and create a new project.
2. Copy the contents of `scripts/google_sheets_api/Code.gs` from this repository into `Code.gs`.
3. Open **Project Settings → Script properties** and add:
   - `SPREADSHEET_ID` = `1vxbWKz0eIlXeGUIkQllxCWBf-9U8_4Td3p1YjCUWI70`
   - `API_TOKEN` = a long random secret (at least 32 random bytes). Do not put this value in the repository or share it publicly.
4. Click **Deploy → New deployment → Web app**. Set **Execute as** to your account and **Who has access** to **Anyone**. The endpoint checks `API_TOKEN` on every request; do not remove that check. Deploy and copy the Web app URL ending in `/exec`.

## 2. Add GitHub Actions secrets

In `GLOBALI90/daily-automation` open **Settings → Secrets and variables → Actions → New repository secret** and add:

- `GOOGLE_SHEETS_API_URL` = the deployed Web app URL ending in `/exec`
- `GOOGLE_SHEETS_API_TOKEN` = exactly the same random secret used as Apps Script `API_TOKEN`

Do not run the collector until both secrets are configured. It intentionally fails before any social provider calls if either is missing.

## 3. Database tabs

- `Leads`: existing 57 rows have been migrated from `data/leads.csv` (30 columns).
- `Personal Profiles`: header row is present; there were no saved personal-profile rows in the previous CSV.

The bridge supports only these two tab names and only `read`/`upsert` operations.
