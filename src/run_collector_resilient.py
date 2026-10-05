import csv
import os
import re
import time
from pathlib import Path

import requests

import lead_collector as collector

ORIGINAL_HEADERS = collector.HEADERS

SEARXNG_FALLBACKS = [
    "https://searxng.website",
    "https://searxng.eshnetwork.space",
    "https://search.mectov.my.id",
]


def _query_variants(query):
    variants = [query]
    simplified = re.sub(r"\s+", " ", query).strip()
    simplified = re.sub(r"\s-\w+", "", simplified)
    simplified = re.sub(r"\bsite:\.cn\b", "", simplified, flags=re.I)
    if simplified and simplified not in variants:
        variants.append(simplified)
    return variants


def _html_results(html, num):
    urls = []
    patterns = [
        r'<a[^>]+class=["\'][^"\']*result_header[^"\']*["\'][^>]+href=["\'](https?://[^"\']+)',
        r'<a[^>]+href=["\'](https?://[^"\']+)["\'][^>]+class=["\'][^"\']*result_header[^"\']*["\']',
    ]
    for pattern in patterns:
        urls.extend(re.findall(pattern, html, flags=re.I))

    out = []
    seen = set()
    for u in urls:
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            out.append({"url": u, "title": "", "content": ""})
            if len(out) >= num:
                break
    return out


def resilient_searx_search(query, num=collector.RESULTS_PER_QUERY, exclude_domains=None):
    bases = []
    configured = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
    if configured:
        bases.append(configured)
    bases.extend(x for x in SEARXNG_FALLBACKS if x not in bases)

    last_error = None

    for variant in _query_variants(query):
        for base in bases:
            # Prefer JSON when the instance enables it; public instances may
            # disable JSON, so fall back to the normal HTML result page.
            try:
                r = requests.get(
                    base + "/search",
                    params={
                        "q": variant,
                        "format": "json",
                        "categories": "general",
                        "language": "en",
                        "pageno": 1,
                    },
                    headers=ORIGINAL_HEADERS,
                    timeout=30,
                )
                if r.ok:
                    results = r.json().get("results", [])[:num]
                    if results:
                        print(f"SearXNG discovery: {base} | results={len(results)}")
                        return results
            except Exception as exc:
                last_error = exc

            try:
                r = requests.get(
                    base + "/search",
                    params={
                        "q": variant,
                        "categories": "general",
                        "language": "en",
                        "pageno": 1,
                    },
                    headers=ORIGINAL_HEADERS,
                    timeout=30,
                )
                r.raise_for_status()
                results = _html_results(r.text, num)
                if results:
                    print(f"SearXNG HTML discovery: {base} | results={len(results)}")
                    return results
            except Exception as exc:
                last_error = exc

    raise RuntimeError(
        f"SearXNG discovery unavailable across {len(bases)} instances: {last_error}"
    )


def resilient_search(query, num=collector.RESULTS_PER_QUERY, exclude_domains=None):
    results = resilient_searx_search(query, num, exclude_domains=exclude_domains)
    return results, "SearXNG"


collector.search = resilient_search


def main():
    region, cities = collector.pick_china_region()
    sector, _ = collector.pick_sector()
    print(f"Search plan: country=China | sector={sector} | region={region}")
    print(f"Region cities: {', '.join(cities)}")
    print(f"Industrial-zone candidates: {', '.join(collector.CHINA_INDUSTRIAL_ZONES)}")

    collector.main()

    output = Path(collector.OUTPUT)
    if output.exists():
        with output.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        run_queries = []
        for row in rows:
            if row.get("run_id") == collector.RUN_ID and row.get("search_query"):
                if row["search_query"] not in run_queries:
                    run_queries.append(row["search_query"])
        print("Actual search queries used this run:")
        for i, q in enumerate(run_queries, start=1):
            print(f"QUERY {i}: {q}")

        fresh = [r for r in rows if r.get("run_id") == collector.RUN_ID]
        if not fresh:
            raise RuntimeError(
                "Search providers returned no fresh leads; failing run instead of reporting false success"
            )


if __name__ == "__main__":
    main()
