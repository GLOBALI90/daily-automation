import csv
import importlib.util
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

SPEC = importlib.util.spec_from_file_location("lead_collector", SRC_DIR / "lead_collector.py")
if SPEC is None or SPEC.loader is None:
    raise ImportError("Could not load src/lead_collector.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)

ORIGINAL_HEADERS = collector.HEADERS


def resilient_searx_search(query, num=collector.RESULTS_PER_QUERY):
    base = os.getenv("SEARXNG_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("SEARXNG_URL is missing")

    last_error = None
    # Render Free services can sleep; the first request may need time to wake the service.
    for attempt, timeout in enumerate((20, 45, 60), start=1):
        try:
            r = requests.get(
                base + "/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                    "language": "en",
                    "pageno": 1,
                },
                headers=ORIGINAL_HEADERS,
                timeout=timeout,
            )
            r.raise_for_status()
            results = r.json().get("results", [])[:num]
            if results:
                print(f"SearXNG backup recovered on attempt {attempt}")
            return results
        except Exception as exc:
            last_error = exc
            print(f"SearXNG backup attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                time.sleep(2)

    print(f"SearXNG backup unavailable after 3 attempts: {last_error}")
    return []


collector.searx_search = resilient_searx_search


def main():
    sector, examples = collector.pick_sector()
    region, cities = collector.pick_china_region()
    print(f"Search plan: country=China | sector={sector} | region={region}")
    print(f"Region cities: {', '.join(cities)}")
    print(f"Industrial-zone candidates: {', '.join(collector.CHINA_INDUSTRIAL_ZONES)}")

    queries = collector.plan_queries(collector.load_existing_domains())
    print("Planned search queries:")
    for i, q in enumerate(queries[:3], start=1):
        print(f"QUERY {i}: {q}")

    # Reuse the exact planned queries for collection while retaining the collector's
    # normal fresh-domain checks and contact extraction.
    rows = []
    seen = set()
    existing_domains = collector.load_existing_domains()
    for q in queries[:3]:
        results, source = collector.search(q, num=collector.RESULTS_PER_QUERY)
        for item in results:
            link = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("content", "")).strip()
            d = collector.domain(link)
            if not d or d in seen or d in existing_domains or collector.looks_like_reject(title, link, snippet):
                continue
            seen.add(d)
            text = f"{title} {snippet}"
            contacts = collector.extract_contacts(link)
            rows.append({
                "company_name": title[:200],
                "website": link,
                "country": collector.TARGET_COUNTRY,
                "industry": collector.infer_product(text),
                "buyer_type": "Potential buyer / industrial consumer",
                "product_interest": collector.infer_product(text),
                "contact_person": "",
                "email": contacts["email"],
                "whatsapp": contacts["whatsapp"],
                "phone": contacts["phone"],
                "linkedin": link if "linkedin.com" in link else "",
                "source": source,
                "evidence": snippet[:500],
                "lead_score": "1" if contacts["email"] or contacts["phone"] or contacts["whatsapp"] else "0",
                "run_id": collector.RUN_ID,
                "collected_at": collector.COLLECTED_AT,
                "search_query": q,
            })
            if len(rows) >= collector.TARGET_LEADS_PER_RUN:
                break
        if len(rows) >= collector.TARGET_LEADS_PER_RUN:
            break

    existing_rows = []
    if collector.OUTPUT.exists():
        with collector.OUTPUT.open(encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))
    existing_keys = {collector.domain(r.get("website", "")) for r in existing_rows if collector.domain(r.get("website", ""))}
    new_rows = [r for r in rows if collector.domain(r.get("website", "")) not in existing_keys]
    all_rows = existing_rows + new_rows
    with collector.OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=collector.FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Collected {len(new_rows)} fresh leads in China focus; total stored: {len(all_rows)}")
    print("Actual search queries used this run:")
    for i, q in enumerate(dict.fromkeys(r["search_query"] for r in new_rows if r.get("search_query")), start=1):
        print(f"QUERY {i}: {q}")


if __name__ == "__main__":
    main()
