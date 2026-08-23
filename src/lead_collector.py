import csv
import json
import os
from pathlib import Path
from urllib.parse import urlparse

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


def google_search(query, num=10):
    key = os.getenv("GOOGLE_CSE_API_KEY")
    cx = os.getenv("GOOGLE_CSE_ID")
    if not key or not cx:
        return []
    r = requests.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": key, "cx": cx, "q": query, "num": min(num, 10)},
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
                rows.append({
                    "company_name": item.get("title", "").split(" | ")[0][:200],
                    "website": link,
                    "country": "",
                    "industry": infer_product(text),
                    "buyer_type": "Potential buyer / industrial consumer",
                    "product_interest": infer_product(text),
                    "contact_person": "",
                    "email": "",
                    "whatsapp": "",
                    "phone": "",
                    "linkedin": link if "linkedin.com" in link else "",
                    "source": "Google Custom Search",
                    "evidence": item.get("snippet", "")[:500],
                    "lead_score": "0"
                })
                if len(rows) >= 10:
                    return rows
    return rows


def main():
    rows = collect()
    exists = OUTPUT.exists()
    with OUTPUT.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)
    print(f"Collected {len(rows)} leads")


if __name__ == "__main__":
    main()
