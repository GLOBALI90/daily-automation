import csv
from html import unescape as html_unescape
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
    # Extract both destination and visible result text. Returning URL-only
    # entries made every HTML-fallback result fail the lead collector's
    # required-title/relevance checks.
    out = []
    seen = set()
    articles = re.findall(r"<article\b.*?</article>", html, flags=re.I | re.S)
    if not articles:
        articles = [html]

    for block in articles:
        anchors = re.findall(
            r"<a\b(?=[^>]*class=[\"'][^\"']*result_header[^\"']*[\"'])([^>]*)>(.*?)</a>",
            block,
            flags=re.I | re.S,
        )
        for attrs, inner in anchors:
            href_match = re.search(r"href=[\"'](https?://[^\"']+)", attrs, flags=re.I)
            if not href_match:
                continue
            url = href_match.group(1).strip()
            if not url or url in seen:
                continue
            title = re.sub(r"<[^>]+>", " ", inner)
            title = re.sub(r"\s+", " ", html_unescape(title)).strip()
            content_match = re.search(
                r"<(?:p|div)[^>]*class=[\"'][^\"']*(?:result-content|content|description)[^\"']*[\"'][^>]*>(.*?)</(?:p|div)>",
                block,
                flags=re.I | re.S,
            )
            snippet = ""
            if content_match:
                snippet = re.sub(r"<[^>]+>", " ", content_match.group(1))
                snippet = re.sub(r"\s+", " ", html_unescape(snippet)).strip()
            if not title:
                continue
            seen.add(url)
            out.append({"url": url, "title": title, "content": snippet})
            if len(out) >= num:
                return out
    return out

def resilient_searx_search(query, num=collector.RESULTS_PER_QUERY, exclude_domains=None):
    bases = []
    configured = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
    if configured:
        bases.append(configured)
    bases.extend(x for x in SEARXNG_FALLBACKS if x not in bases)

    last_error = None
    # Bound fallback discovery time so a slow public instance cannot consume
    # the entire GitHub Actions job timeout. This is a per-query budget.
    deadline = time.monotonic() + 45

    for variant in _query_variants(query):
        for base in bases:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            request_timeout = max(1, min(8, remaining))
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
                    timeout=request_timeout,
                )
                if r.ok:
                    results = r.json().get("results", [])[:num]
                    if results:
                        print(f"SearXNG discovery: {base} | results={len(results)}")
                        return results
            except Exception as exc:
                last_error = exc

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            request_timeout = max(1, min(8, remaining))
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
                    timeout=request_timeout,
                )
                r.raise_for_status()
                results = _html_results(r.text, num)
                if results:
                    print(f"SearXNG HTML discovery: {base} | results={len(results)}")
                    return results
            except Exception as exc:
                last_error = exc

    if time.monotonic() >= deadline:
        print("SearXNG fallback reached the 45-second per-query time budget")
    raise RuntimeError(
        f"SearXNG discovery unavailable within the bounded search budget across {len(bases)} instances: {last_error}"
    )


def resilient_search(query, num=collector.RESULTS_PER_QUERY, exclude_domains=None):
    # Prefer the configured You.com API. The previous wrapper replaced
    # collector.search with SearXNG-only search, unintentionally bypassing
    # You.com even when YDC_API_KEY was configured.
    try:
        results = collector.you_search(
            query, num=num, exclude_domains=exclude_domains
        )
        if results:
            print(f"You.com discovery: results={len(results)}")
            return results, "You.com"
        print("You.com discovery returned 0 results; falling back to SearXNG")
    except Exception as exc:
        print(f"You.com discovery unavailable ({type(exc).__name__}); falling back to SearXNG")

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
