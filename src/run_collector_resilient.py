import csv
import os
import re
import time
from pathlib import Path

import requests

# The workflow executes this file directly from src/. Import the collector module
# as a local module so it works both in GitHub Actions and when run manually.
import lead_collector as collector

ORIGINAL_HEADERS = collector.HEADERS


def _normalize_you_results(data, num):
    web = (data.get("results") or {}).get("web") or []
    return [
        {
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "content": item.get("description", "") or item.get("snippet", ""),
        }
        for item in web[:num]
    ]


def resilient_you_search(query, num=collector.RESULTS_PER_QUERY):
    key = os.getenv("YDC_API_KEY")
    if not key:
        raise RuntimeError("YDC_API_KEY is missing")

    last_error = None
    # You.com documents POST /v1/search as the current interface; keep GET as
    # compatibility fallback because existing integrations still support it.
    for attempt in range(1, 4):
        try:
            r = requests.post(
                "https://api.you.com/v1/search",
                json={"query": query, "count": min(num, 100)},
                headers={
                    "X-API-Key": key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            r.raise_for_status()
            results = _normalize_you_results(r.json(), num)
            if results:
                print(f"You.com recovered with POST on attempt {attempt}: {len(results)} results")
                return results
            print(f"You.com POST attempt {attempt}/3 returned 0 results")
        except Exception as exc:
            last_error = exc
            print(f"You.com POST attempt {attempt}/3 failed: {exc}")

        try:
            r = requests.get(
                "https://api.you.com/v1/search",
                params={"query": query, "count": min(num, 100)},
                headers={"X-API-Key": key, "Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            results = _normalize_you_results(r.json(), num)
            if results:
                print(f"You.com recovered with GET on attempt {attempt}: {len(results)} results")
                return results
            print(f"You.com GET attempt {attempt}/3 returned 0 results")
        except Exception as exc:
            last_error = exc
            print(f"You.com GET attempt {attempt}/3 failed: {exc}")

        if attempt < 3:
            time.sleep(2 * attempt)

    raise RuntimeError(f"You.com search unavailable after 3 attempts: {last_error}")


def _query_variants(query):
    variants = [query]
    # SearXNG may propagate operators to engines that interpret them
    # differently. Keep the AI query first, then retry with a less restrictive
    # version that preserves the China/region/industry intent.
    simplified = re.sub(r"\s+", " ", query).strip()
    simplified = re.sub(r"\s-\w+", "", simplified)
    simplified = re.sub(r"\bsite:\.cn\b", "", simplified, flags=re.I)
    if simplified and simplified not in variants:
        variants.append(simplified)
    return variants


def resilient_searx_search(query, num=collector.RESULTS_PER_QUERY):
    base = os.getenv("SEARXNG_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("SEARXNG_URL is missing")

    last_error = None
    variants = _query_variants(query)
    for variant in variants:
        for attempt, timeout in enumerate((20, 45, 60), start=1):
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
                    timeout=timeout,
                )
                r.raise_for_status()
                results = r.json().get("results", [])[:num]
                if results:
                    print(f"SearXNG backup recovered on attempt {attempt}: {len(results)} results")
                    return results
                last_error = RuntimeError("SearXNG returned no usable results")
                print(f"SearXNG backup attempt {attempt}/3 returned 0 usable results")
            except Exception as exc:
                last_error = exc
                print(f"SearXNG backup attempt {attempt}/3 failed: {exc}")
            if attempt < 3:
                time.sleep(2)

    raise RuntimeError(f"SearXNG backup unavailable after {len(variants)} query variants: {last_error}")


collector.you_search = resilient_you_search
collector.searx_search = resilient_searx_search


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

    # A Search run that finds nothing because every provider failed is not a
    # successful collection run. The existing collector writes an empty result
    # set in that situation, so fail explicitly here to expose the outage.
    if output.exists():
        with output.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        fresh = [r for r in rows if r.get("run_id") == collector.RUN_ID]
        if not fresh:
            raise RuntimeError("Search providers returned no fresh leads; failing run instead of reporting false success")


if __name__ == "__main__":
    main()
