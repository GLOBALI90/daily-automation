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
    '"purchasing manager" chemicals importer company',
    '"raw material" "petroleum products" importer company',
    '"steel" "procurement" industrial company',
    '"renewable energy" "procurement" company',
    '"petrochemical" manufacturer procurement',
    '"chemical" manufacturer purchasing',
    '"industrial consumer" petroleum products',
    '"importer" "petroleum products" company',
    '"importer" chemicals industrial company',
]

FIELDS = [
    "company_name", "website", "country", "industry", "buyer_type",
    "product_interest", "contact_person", "email", "whatsapp", "phone",
    "linkedin", "source", "evidence", "lead_score"
]

HEADERS = {"User-Agent": "ROZHAN-Global-B2B-Research/1.0 (+https://www.rojanglobal.com)"}
SOCIAL_DOMAINS = {"linkedin.com", "facebook.com", "x.com", "twitter.com", "instagram.com"}


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


def searx_search(query, num=10):
    base = os.getenv("SEARXNG_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("SEARXNG_URL is missing")
    r = requests.get(
        base + "/search",
        params={"q": query, "format": "json", "categories": "general", "language": "en", "pageno": 1},
        headers=HEADERS,
        timeout=45,
    )
    r.raise_for_status()
    return r.json().get("results", [])[:num]


def you_search(query, num=10):
    key = os.getenv("YDC_API_KEY")
    if not key:
        raise RuntimeError("YDC_API_KEY is missing")
    r = requests.get(
        "https://api.you.com/v1/search",
        params={"query": query, "count": min(num, 100)},
        headers={"X-API-Key": key, "Accept": "application/json"},
        timeout=45,
    )
    r.raise_for_status()
    data = r.json()
    web = (data.get("results") or {}).get("web") or []
    results = []
    for item in web:
        results.append({
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "content": item.get("description", "") or item.get("snippet", ""),
        })
    return results[:num]


def search(query, num=10):
    try:
        results = searx_search(query, num)
        if results:
            return results, "SearXNG"
    except Exception as exc:
        print(f"SearXNG unavailable: {exc}")

    try:
        results = you_search(query, num)
        if results:
            return results, "You.com"
    except Exception as exc:
        print(f"You.com backup unavailable: {exc}")

    return [], "none"


def extract_contacts(url):
    result = {"email": "", "whatsapp": "", "phone": ""}
    d = domain(url)
    if not url or d in SOCIAL_DOMAINS:
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
    rows = []
    seen = set()
    for q in QUERIES:
        results, source = search(q, num=10)
        for item in results:
            link = str(item.get("url", "")).strip()
            d = domain(link)
            if not d or d in seen:
                continue
            seen.add(d)
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("content", "")).strip()
            text = f"{title} {snippet}"
            contacts = extract_contacts(link)
            rows.append({
                "company_name": title[:200],
                "website": link,
                "country": "",
                "industry": infer_product(text),
                "buyer_type": "Potential buyer / industrial consumer",
                "product_interest": infer_product(text),
                "contact_person": "",
                "email": contacts["email"],
                "whatsapp": contacts["whatsapp"],
                "phone": contacts["phone"],
                "linkedin": link if "linkedin.com" in link else "",
                "source": source,
                "evidence": snippet[:500],
                "lead_score": "1" if contacts["email"] or contacts["phone"] or contacts["whatsapp"] else "0",
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
