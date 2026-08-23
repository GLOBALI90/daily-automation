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

QUERIES = [
    '"procurement" "petrochemical" buyer company',
    '"purchasing manager" chemicals importer',
    '"raw material" "petroleum products" importer',
    '"steel" "procurement" industrial company',
    '"renewable energy" "procurement" company',
]

SITES = [
    "linkedin.com/company",
    "facebook.com",
    "x.com",
    "twitter.com",
    "instagram.com",
]

FIELDS = [
    "company_name", "website", "country", "industry", "buyer_type",
    "product_interest", "contact_person", "email", "whatsapp", "phone",
    "linkedin", "source", "evidence", "lead_score"
]

HEADERS = {"User-Agent": "ROZHAN-Global-B2B-Research/1.0 (+https://www.rojanglobal.com)"}


def google_search(query, num=10):
    key = os.getenv("GOOGLE_CSE_API_KEY")
    cx = os.getenv("GOOGLE_CSE_ID")
    if not key or not cx:
        return []
    r = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": key, "cx": cx, "q": query, "num": min(num, 10)},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("items", [])


def domain(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def infer_product(text):
    t = text.lower()
    for word, product in [
        ("petrochemical", "Petrochemicals"),
        ("chemical", "Chemicals"),
        ("petroleum", "Petroleum products"),
        ("steel", "Steel"),
        ("renewable", "Renewable energy"),
    ]:
        if word in t:
            return product
    return ""


def extract_contacts(url):
    result = {"email": "", "whatsapp": "", "phone": ""}
    if not url or domain(url) in {"linkedin.com", "facebook.com", "x.com", "twitter.com", "instagram.com"}:
        return result
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        html = r.text[:500_000]
    except Exception:
        return result

    emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", html, re.I)
    for email in emails:
        e = email.lower()
        if not e.endswith(("@example.com",)):
            result["email"] = e
            break

    wa = re.findall(r"(?:https?://)?(?:wa\.me/|api\.whatsapp\.com/send\?phone=)([0-9+]{8,20})", html, re.I)
    if wa:
        result["whatsapp"] = wa[0]

    phones = re.findall(r"(?:\+?\d[\d .()/-]{7,}\d)", html)
    if phones:
        result["phone"] = re.sub(r"\s+", " ", phones[0]).strip()

    return result


def collect():
    rows = []
    seen = set()
    for q in QUERIES:
        for site in SITES:
            results = google_search(f"{q} site:{site}")
            for item in results:
                link = item.get("link", "")
                d = domain(link)
                if not d or d in seen:
                    continue
                seen.add(d)
                text = f"{item.get('title','')} {item.get('snippet','')}"
                contacts = extract_contacts(link)
                website = link
                if d.startswith("linkedin.com"):
                    website = item.get("pagemap", {}).get("metatags", [{}])[0].get("og:url", link)
                rows.append({
                    "company_name": item.get("title", "").split(" | ")[0][:200],
                    "website": website,
                    "country": "",
                    "industry": infer_product(text),
                    "buyer_type": "Potential buyer / industrial consumer",
                    "product_interest": infer_product(text),
                    "contact_person": "",
                    "email": contacts["email"],
                    "whatsapp": contacts["whatsapp"],
                    "phone": contacts["phone"],
                    "linkedin": link if "linkedin.com" in link else "",
                    "source": "Google Custom Search",
                    "evidence": item.get("snippet", "")[:500],
                    "lead_score": "1" if contacts["email"] or contacts["phone"] or contacts["whatsapp"] else "0"
                })
                if len(rows) >= 10:
                    return rows
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
