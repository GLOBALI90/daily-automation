import csv
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
COMPANY = json.loads((ROOT / "config/company.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
OUTPUT = DATA / "leads.csv"

FIELDS = [
    "company_name", "website", "country", "industry", "buyer_type",
    "product_interest", "contact_person", "email", "whatsapp", "phone",
    "linkedin", "source", "evidence", "lead_score"
]

HEADERS = {"User-Agent": "ROZHAN-Global-B2B-Research/1.0 (+https://www.rojanglobal.com)"}
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL = os.getenv("GEMINI_SEARCH_MODEL", "gemini-3.5-flash-lite")

PROMPT = f"""
You are the web research assistant for {COMPANY['brand']} ({COMPANY['legal_name']}).
Business sectors: petroleum products, chemicals, petrochemicals, steel, renewable energy.
Target customers: direct buyers, industrial consumers, raw-material consumers, importers and procurement companies.

Use Google Search grounding to find exactly 10 distinct potential B2B buyer companies anywhere in the world.
Prefer real operating companies that appear to purchase, import, consume or procure products in our sectors.
Do not restrict yourself to social networks. Use company websites and public business pages when available.
Do not invent contact details.

Return ONLY a JSON array with exactly 10 objects, using these keys:
company_name, website, country, industry, buyer_type, product_interest, contact_person, email, whatsapp, phone, linkedin, source, evidence

Rules:
- website must be a public company or relevant business webpage when available.
- email, whatsapp, phone and contact_person must be publicly stated facts or empty strings.
- linkedin may contain a public LinkedIn company/profile URL or empty string.
- evidence should briefly explain why the company appears relevant and should reflect information found in search results.
- Do not fabricate prices, volumes, names or contact information.
""".strip()


def gemini_search():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is missing")
        return []

    url = f"{GEMINI_BASE}/models/{MODEL}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    try:
        r = requests.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0].get("text", "")
        if not text:
            return []
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            return []
        rows = json.loads(text[start:end + 1])
        return rows if isinstance(rows, list) else []
    except Exception as exc:
        print(f"Gemini Google Search failed: {exc}")
        return []


def domain(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def extract_contacts(url):
    result = {"email": "", "whatsapp": "", "phone": ""}
    if not url:
        return result
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()
        html = r.text[:500_000]
        base = r.url
    except Exception:
        return result

    emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", html, re.I)
    for email in emails:
        e = email.lower()
        if not e.endswith("@example.com"):
            result["email"] = e
            break

    wa = re.findall(r"(?:https?://)?(?:wa\.me/|api\.whatsapp\.com/send\?phone=)([0-9+]{8,20})", html, re.I)
    if wa:
        result["whatsapp"] = wa[0]

    phones = re.findall(r"(?:\+?\d[\d .()/-]{7,}\d)", html)
    if phones:
        result["phone"] = re.sub(r"\s+", " ", phones[0]).strip()

    if not result["email"]:
        for path in ("/contact", "/contact-us", "/contacts"):
            try:
                contact_url = urljoin(base, path)
                cr = requests.get(contact_url, headers=HEADERS, timeout=15, allow_redirects=True)
                if not cr.ok:
                    continue
                chtml = cr.text[:300_000]
                emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", chtml, re.I)
                for email in emails:
                    e = email.lower()
                    if not e.endswith("@example.com"):
                        result["email"] = e
                        break
                if result["email"]:
                    break
            except Exception:
                pass

    return result


def collect():
    candidates = gemini_search()[:10]
    rows = []
    seen = set()

    for item in candidates:
        if not isinstance(item, dict):
            continue
        website = str(item.get("website", "")).strip()
        key = domain(website) or str(item.get("company_name", "")).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)

        contacts = extract_contacts(website)
        row = {
            "company_name": str(item.get("company_name", "")).strip()[:200],
            "website": website,
            "country": str(item.get("country", "")).strip(),
            "industry": str(item.get("industry", "")).strip(),
            "buyer_type": str(item.get("buyer_type", "Potential buyer / industrial consumer")).strip(),
            "product_interest": str(item.get("product_interest", "")).strip(),
            "contact_person": str(item.get("contact_person", "")).strip(),
            "email": str(item.get("email", "")).strip() or contacts["email"],
            "whatsapp": str(item.get("whatsapp", "")).strip() or contacts["whatsapp"],
            "phone": str(item.get("phone", "")).strip() or contacts["phone"],
            "linkedin": str(item.get("linkedin", "")).strip(),
            "source": "Gemini + Google Search grounding",
            "evidence": str(item.get("evidence", "")).strip()[:500],
            "lead_score": "1" if (item.get("email") or item.get("phone") or item.get("whatsapp") or contacts["email"] or contacts["phone"] or contacts["whatsapp"]) else "0",
        }
        if row["company_name"]:
            rows.append(row)
        if len(rows) >= 10:
            break
    return rows


def main():
    rows = collect()
    existing_rows = []
    if OUTPUT.exists():
        with OUTPUT.open(encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))
    existing_keys = {r.get("website") for r in existing_rows if r.get("website")}
    new_rows = [r for r in rows if r.get("website") not in existing_keys]
    all_rows = existing_rows + new_rows
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Collected {len(new_rows)} new leads; total stored: {len(all_rows)}")


if __name__ == "__main__":
    main()
