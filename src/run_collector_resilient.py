import csv
import os
import time
from pathlib import Path

import requests

# The workflow executes this file directly from src/. Import the collector module
# as a local module so it works both in GitHub Actions and when run manually.
import lead_collector as collector

ORIGINAL_HEADERS = collector.HEADERS


def resilient_searx_search(query, num=collector.RESULTS_PER_QUERY):
    base = os.getenv("SEARXNG_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("SEARXNG_URL is missing")

    last_error = None
    # Render Free services can sleep. Give the service a few chances to wake up.
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
                print(f"SearXNG backup recovered on attempt {attempt}: {len(results)} results")
                return results
            last_error = RuntimeError("SearXNG returned no usable results")
            print(f"SearXNG backup attempt {attempt}/3 returned 0 usable results")
        except Exception as exc:
            last_error = exc
            print(f"SearXNG backup attempt {attempt}/3 failed: {exc}")
        if attempt < 3:
            time.sleep(2)

    raise RuntimeError(f"SearXNG backup unavailable after 3 attempts: {last_error}")


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
