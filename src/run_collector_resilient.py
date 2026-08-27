import csv
import os
import time
from pathlib import Path

import requests

from src import lead_collector as collector


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

    raise RuntimeError(f"SearXNG backup unavailable after 3 attempts: {last_error}")


collector.searx_search = resilient_searx_search


def main():
    sector, cities = collector.pick_sector(), collector.pick_china_region()[1]
    region, _ = collector.pick_china_region()
    print(f"Search plan: country=China | sector={sector[0]} | region={region}")
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


if __name__ == "__main__":
    main()
