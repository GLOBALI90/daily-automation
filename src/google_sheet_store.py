"""Minimal authenticated client for the Google Apps Script Sheets bridge."""
import os
import requests


class GoogleSheetStore:
    def __init__(self, endpoint, token, timeout=60):
        self.endpoint = str(endpoint or "").strip()
        self.token = str(token or "").strip()
        self.timeout = timeout
        if not self.endpoint or not self.token:
            raise RuntimeError(
                "Google Sheets storage is required but not configured. Set GitHub Actions secrets "
                "GOOGLE_SHEETS_API_URL and GOOGLE_SHEETS_API_TOKEN. No social providers were called."
            )

    @classmethod
    def from_env(cls):
        return cls(os.getenv("GOOGLE_SHEETS_API_URL"), os.getenv("GOOGLE_SHEETS_API_TOKEN"))

    def _request(self, action, tab, **extra):
        payload = {"action": action, "tab": tab, "token": self.token}
        payload.update(extra)
        try:
            response = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            raise RuntimeError(f"Google Sheets {action} failed for {tab}: {type(exc).__name__}: {exc}") from exc
        if not isinstance(result, dict) or not result.get("ok"):
            message = result.get("error", "invalid response") if isinstance(result, dict) else "invalid response"
            raise RuntimeError(f"Google Sheets {action} failed for {tab}: {message}")
        return result

    def read_table(self, tab):
        result = self._request("read", tab)
        headers = [str(h) for h in result.get("headers", []) if str(h).strip()]
        rows = result.get("rows", [])
        if not isinstance(rows, list):
            raise RuntimeError(f"Google Sheets returned invalid rows for {tab}")
        return [
            {h: str(row.get(h, "") if row.get(h) is not None else "") for h in headers}
            for row in rows if isinstance(row, dict)
        ]

    def upsert_table(self, tab, rows, headers, key_column):
        clean_headers = list(dict.fromkeys(str(h) for h in headers if str(h).strip()))
        clean_rows = [
            {h: str(row.get(h, "") if row.get(h) is not None else "") for h in clean_headers}
            for row in rows
        ]
        return self._request(
            "upsert", tab, headers=clean_headers, rows=clean_rows, key_column=key_column
        )
