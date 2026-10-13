import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from gemini_utils import generate_text

ROOT = Path(__file__).resolve().parents[1]
COMPANY = json.loads((ROOT / "config/company.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
OUTPUT = DATA / "leads.csv"

def fallback_queries():
    sector, examples = pick_sector()
    region, cities = pick_china_region()
    city_a = cities[0]
    city_b = cities[1] if len(cities) > 1 else cities[0]
    zone = CHINA_INDUSTRIAL_ZONES[(len(cities) + len(region)) % len(CHINA_INDUSTRIAL_ZONES)]
    return [
        f'"{sector}" procurement purchasing importer manufacturer "{region}" "{city_a}" China contact -jobs -careers -hiring -article -blog -directory -news',
        f'"{sector}" "industrial park" "{region}" China factory purchasing sourcing procurement "{zone}" -jobs -careers -article -blog -directory -news',
        f'"{sector}" industrial consumer buyer supplier "{city_a}" OR "{city_b}" China company contact procurement -jobs -careers -article -blog -directory -news',
    ]

SECTORS = [
    ("petroleum products", "refineries, fuel distributors, petroleum importers, oil & gas industrial consumers"),
    ("chemicals", "chemical manufacturers, industrial chemical consumers, chemical importers"),
    ("petrochemicals", "petrochemical manufacturers, polymer/feedstock consumers, petrochemical procurement teams"),
    ("steel", "steel processors, fabricators, mills, construction and industrial consumers, steel importers"),
    ("renewable energy", "solar, wind, battery and renewable-energy project companies with procurement needs"),
]

CHINA_REGIONS = [
    ("Guangdong", ["Guangzhou", "Shenzhen", "Foshan", "Dongguan", "Huizhou", "Zhanjiang"]),
    ("Jiangsu", ["Suzhou", "Nanjing", "Wuxi", "Changzhou", "Nantong"]),
    ("Zhejiang", ["Ningbo", "Hangzhou", "Shaoxing", "Jiaxing", "Taizhou"]),
    ("Shandong", ["Qingdao", "Dongying", "Yantai", "Weifang", "Jinan"]),
    ("Shanghai", ["Shanghai"]),
    ("Tianjin", ["Tianjin"]),
    ("Hebei", ["Tangshan", "Cangzhou", "Shijiazhuang"]),
    ("Liaoning", ["Dalian", "Shenyang", "Yingkou"]),
    ("Fujian", ["Xiamen", "Quanzhou", "Fuzhou"]),
    ("Hubei", ["Wuhan", "Yichang"]),
]

CHINA_INDUSTRIAL_ZONES = [
    "Shanghai Chemical Industry Park",
    "Ningbo Petrochemical Economic and Technological Development Zone",
    "Huizhou Daya Bay Petrochemical Industrial Zone",
    "Nanjing Jiangbei New Area chemical and advanced manufacturing clusters",
    "Tianjin Nangang Industrial Zone",
    "Zhanjiang Economic and Technological Development Zone",
    "Dongying Kenli petrochemical industrial clusters",
    "Cangzhou Lingang Economic and Technological Development Zone",
    "Ningbo Economic and Technological Development Zone",
    "Suzhou Industrial Park",
    "Guangzhou Nansha industrial clusters",
    "Foshan high-tech and advanced manufacturing industrial clusters",
    "Tangshan Caofeidian Industrial Zone",
    "Dalian Changxing Island Economic and Technological Development Zone",
]

FIELDS = [
    "company_name", "website", "country", "industry", "buyer_type",
    "product_interest", "contact_person", "email", "whatsapp", "phone",
    "linkedin", "source", "evidence", "lead_score", "run_id", "collected_at", "search_query", "discovery_method"
]

HEADERS = {"User-Agent": "ROZHAN-Global-B2B-Research/1.0 (+https://www.rojanglobal.com)"}
SOCIAL_DOMAINS = {"linkedin.com", "facebook.com", "x.com", "twitter.com"}
EXCLUDED_DOMAINS = {
    "indeed.com", "glassdoor.com", "ziprecruiter.com", "jobleads.com", "bebee.com",
    "michaelpage.com", "westlaketalent.com", "talents.vaia.com", "claytonpersonnel.com",
    "pointtobusinessservices.com", "pndatasol.com", "bluemailmedia.com", "datamarketersgroup.com",
    "thomasnet.com", "petrochemical.com", "lightsource.ai", "quora.com",
    "datacaptive.com", "averickmedia.com", "fountmedia.com", "bizinforusa.com",
    "tradewheel.com", "go4worldbusiness.com", "youtube.com", "youtu.be",
    "wikipedia.org", "baike.baidu.com", "baidu.com", "tripadvisor.com",
    "chinadiscovery.com", "forums.autodesk.com", "autodesk.com",
    "slocounty.ca.gov", "elaws.us", "municode.com", "reddit.com",
}
NON_COMPANY_TITLE_WORDS = {
    "procurement", "purchasing", "contact us", "about us", "request a quote",
    "jobs", "careers", "career", "news", "article", "blog", "directory",
    "market report", "supplier directory", "top suppliers", "list of",
}

EXCLUDED_WORDS = {
    "job", "jobs", "career", "careers", "hiring", "vacancy", "vacancies", "employment",
    "recruit", "recruitment", "article", "blog", "guide", "directory", "list", "email list",
    "course", "webinar", "press release", "news", "magazine",
}
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
TARGET_LEADS_PER_RUN = 20
RESULTS_PER_QUERY = 20
TARGET_COUNTRY = "China"
RUN_ID = os.getenv("GITHUB_RUN_ID", datetime.now(timezone.utc).strftime("manual-%Y%m%d%H%M%S"))
COLLECTED_AT = datetime.now(timezone.utc).isoformat()


def domain(url):
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


INDUSTRY_TERMS = (
    "chemical", "chemicals", "petrochemical", "petrochemicals", "petroleum",
    "oil and gas", "oil & gas", "refinery", "refineries", "steel", "metal",
    "industrial", "manufacturer", "manufacturing", "factory", "factories",
    "polymer", "resin", "fertilizer", "solvent", "coating", "solar", "wind",
    "battery", "renewable energy", "raw material", "feedstock"
)
BUYER_TERMS = (
    "company", "group", "limited", "ltd", "corporation", "corp", "manufacturer",
    "manufacturing", "factory", "industrial", "supplier", "production",
    "procurement", "importer", "refinery", "plant", "products", "materials"
)
CHINA_SIGNALS = (
    "china", "chinese", "jiangsu", "guangdong", "zhejiang", "shandong",
    "shanghai", "tianjin", "hebei", "liaoning", "fujian", "hubei",
    "suzhou", "nanjing", "wuxi", "changzhou", "nantong", "guangzhou",
    "shenzhen", "foshan", "dongguan", "huizhou", "ningbo", "hangzhou",
    "shaoxing", "jiaxing", "qingdao", "dongying", "yantai", "weifang",
    "jinan", "tangshan", "cangzhou", "dalian", "shenyang", "yingkou",
    "xiamen", "quanzhou", "fuzhou", "wuhan", "yichang"
)

def looks_like_reject(title, url, snippet, query=""):
    d = domain(url).lower().split(":")[0]
    if not d or not title.strip():
        return True
    if any(d == blocked or d.endswith("." + blocked) for blocked in EXCLUDED_DOMAINS):
        return True
    if any(d == social or d.endswith("." + social) for social in SOCIAL_DOMAINS):
        return True

    title_text = title.lower().strip()
    path_text = urlparse(url).path.lower()
    if any(marker in path_text for marker in (
        "/wiki/", "/tourism", "/tour/", "/forum/", "/forums/", "/community/",
        "/watch", "/video", "/reel/", "/profile/", "/search", "/jobs/", "/careers/"
    )):
        return True

    # Reject editorial/recruitment results based primarily on their title/path,
    # not on incidental words in a company's descriptive snippet.
    title_reject = {
        "job", "jobs", "career", "careers", "hiring", "vacancy", "vacancies",
        "employment", "recruitment", "article", "blog", "guide", "directory",
        "email list", "course", "webinar", "press release", "news", "magazine",
        "market report", "supplier directory", "top suppliers", "list of"
    }
    if any(re.search(r"\b" + re.escape(word) + r"\b", title_text) for word in title_reject):
        return True
    if any(word in title_text for word in NON_COMPANY_TITLE_WORDS):
        return True

    text = f"{title} {snippet}".lower()
    if not any(term in text for term in INDUSTRY_TERMS):
        return True

    strong_buyer_signals = (
        "manufacturer", "manufacturing", "factory", "factories", "supplier",
        "suppliers", "procurement", "purchasing", "importer", "importers",
        "refinery", "refineries", "plant", "plants", "limited", "ltd",
        "corporation", " corp", "company", "group", "chemicals", "chemical",
        "steel", "petrochemical", "petroleum", "industrial materials"
    )
    if not any(signal in text for signal in strong_buyer_signals):
        return True

    # A China-focused search query is itself useful geographic context: many
    # legitimate .com company pages do not repeat "China" in their snippets.
    query_context = f"{query} {text}".lower()
    if not d.endswith(".cn") and not any(signal in query_context for signal in CHINA_SIGNALS):
        return True
    return False

def load_existing_domains():
    if not OUTPUT.exists():
        return set()
    try:
        with OUTPUT.open(encoding="utf-8") as f:
            return {domain(r.get("website", "")) for r in csv.DictReader(f) if domain(r.get("website", ""))}
    except Exception:
        return set()


def load_recent_domains(limit=450):
    if not OUTPUT.exists():
        return []
    try:
        with OUTPUT.open(encoding="utf-8") as f:
            domains = [domain(r.get("website", "")) for r in csv.DictReader(f)]
        ordered = []
        seen = set()
        for d in reversed(domains):
            if d and d not in seen:
                seen.add(d)
                ordered.append(d)
            if len(ordered) >= limit:
                break
        return ordered
    except Exception:
        return []


def pick_sector():
    existing_count = 0
    if OUTPUT.exists():
        try:
            with OUTPUT.open(encoding="utf-8") as f:
                existing_count = max(sum(1 for _ in f) - 1, 0)
        except Exception:
            pass
    index = existing_count // max(TARGET_LEADS_PER_RUN, 1)
    return SECTORS[index % len(SECTORS)]


def pick_china_region():
    existing_count = 0
    if OUTPUT.exists():
        try:
            with OUTPUT.open(encoding="utf-8") as f:
                existing_count = max(sum(1 for _ in f) - 1, 0)
        except Exception:
            pass
    index = existing_count // max(TARGET_LEADS_PER_RUN, 1)
    return CHINA_REGIONS[index % len(CHINA_REGIONS)]


def plan_queries(existing_domains):
    key = os.getenv("GEMINI_API_KEY")
    if not key or not GEMINI_MODEL:
        return fallback_queries()

    sector, examples = pick_sector()
    region, cities = pick_china_region()
    city_text = ", ".join(cities)
    zone_text = ", ".join(CHINA_INDUSTRIAL_ZONES)
    excluded_text = ", ".join(sorted(existing_domains)[-80:])
    prompt = f"""You are the search planner for {COMPANY['brand']} ({COMPANY['legal_name']}).
TARGET COUNTRY: China only.
Business sectors: petroleum products, chemicals, petrochemicals, steel, renewable energy.
Target customers: direct buyers, industrial consumers, raw-material consumers, importers and procurement companies.
This run should prioritize: {sector} ({examples}).
Geographic focus for this run: China, {region}; prioritize these cities/districts where relevant: {city_text}.
Also actively search industrial parks, industrial estates, economic and technological development zones, chemical parks, petrochemical zones, steel bases, manufacturing clusters and factory districts in China.
Examples of useful Chinese industrial locations (use as leads, not as an exhaustive list): {zone_text}.
Create exactly 3 concise web-search queries for NEW Chinese companies not already used in previous runs.
At least 1 query MUST target a city/region or industrial zone.
At least 1 query MUST target an industrial park / development zone / chemical park / manufacturing cluster.
Use buyer intent: procurement, purchasing, sourcing, importer, industrial consumer, plant, manufacturer, raw materials, factory.
Prefer real operating company websites and procurement/contact pages.
Do NOT search job boards, job posts, career pages, recruitment pages, articles, blogs, news, courses, webinars, generic directories, email-list sellers, lead-list vendors, social profiles, or marketplaces.
Use negative terms such as -jobs -careers -hiring -article -blog -directory -list when useful.
Previously used domains that MUST be avoided: {excluded_text}
Return ONLY a JSON array of 3 strings."""
    try:
        text = generate_text(prompt, api_key=key, model=GEMINI_MODEL)
        if not text:
            raise RuntimeError("Gemini returned an empty response")
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1:
            queries = json.loads(text[start:end + 1])
            if isinstance(queries, list):
                queries = [str(q).strip() for q in queries if str(q).strip()]
                if len(queries) >= 3:
                    print(f"Gemini planned 3 search queries for sector: {sector} | China region: {region}")
                    return queries[:3]
    except Exception as exc:
        print(f"Gemini query planning failed: {type(exc).__name__}: {exc}")
    return fallback_queries()


def you_search(query, num=RESULTS_PER_QUERY, exclude_domains=None):
    key = os.getenv("YDC_API_KEY")
    if not key:
        raise RuntimeError("YDC_API_KEY is missing")
    # You.com Web Search API is hosted at ydc-index.io. Use POST for
    # JSON request bodies; api.you.com is for Answer/Research APIs.
    r = requests.post(
        "https://ydc-index.io/v1/search",
        json={"query": query, "count": min(num, 100), "exclude_domains": (exclude_domains or [])[:500]},
        headers={"X-API-Key": key, "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    web = (data.get("results") or {}).get("web") or []
    return [{"url": item.get("url", ""), "title": item.get("title", ""), "content": item.get("description", "") or item.get("snippet", "")} for item in web[:num]]


def searx_search(query, num=RESULTS_PER_QUERY):
    base = os.getenv("SEARXNG_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("SEARXNG_URL is missing")
    last_error = None
    for attempt, timeout in enumerate((20, 45, 60), start=1):
        try:
            r = requests.get(base + "/search", params={"q": query, "format": "json", "categories": "general", "language": "en", "pageno": 1}, headers=HEADERS, timeout=timeout)
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


def search(query, num=RESULTS_PER_QUERY, exclude_domains=None):
    try:
        results = you_search(query, num, exclude_domains=exclude_domains)
        if results:
            print(f"Search provider: You.com | results={len(results)}")
            return results, "You.com"
        print("You.com primary returned 0 results")
    except Exception as exc:
        print(f"You.com primary unavailable: {exc}")
    try:
        results = searx_search(query, num)
        if results:
            return results, "SearXNG"
    except Exception as exc:
        print(f"SearXNG backup unavailable: {exc}")
    return [], "none"


def infer_product(text):
    t = text.lower()
    for word, product in [("petrochemical", "Petrochemicals"), ("chemical", "Chemicals"), ("petroleum", "Petroleum products"), ("steel", "Steel"), ("renewable", "Renewable energy")]:
        if word in t:
            return product
    return ""


def extract_contacts(url):
    result = {"email": "", "whatsapp": "", "phone": ""}
    d = domain(url)
    if not url or d in SOCIAL_DOMAINS:
        return result
    try:
        # Contact enrichment is best-effort: keep each lead bounded so a slow
        # target website cannot stall the whole collection run.
        r = requests.get(url, headers=HEADERS, timeout=6, allow_redirects=True)
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
        for path in ("/contact", "/contact-us"):
            try:
                contact_url = urljoin(base, path)
                cr = requests.get(contact_url, headers=HEADERS, timeout=3, allow_redirects=True)
                if not cr.ok:
                    continue
                chtml = cr.text[:300_000]
                for email in re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", chtml, re.I):
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
    existing_domains = load_existing_domains()
    recent_excludes = load_recent_domains(limit=450)
    queries = plan_queries(existing_domains)
    print(
        "LEAD_COLLECTION_CONFIG " + json.dumps({
            "queries_planned": len(queries),
            "existing_domains": len(existing_domains),
            "recent_domains_excluded": len(recent_excludes),
            "target_leads": TARGET_LEADS_PER_RUN,
            "results_per_query": RESULTS_PER_QUERY,
        }, ensure_ascii=False)
    )
    for query_index, q in enumerate(queries, start=1):
        results, source = search(q, num=RESULTS_PER_QUERY, exclude_domains=recent_excludes)
        stats = {
            "results_returned": len(results),
            "invalid_url": 0,
            "duplicate_in_run": 0,
            "already_stored_domain": 0,
            "filtered_not_target": 0,
            "accepted": 0,
        }
        print(
            f"LEAD_QUERY_AUDIT query={query_index} source={source} "
            f"results={len(results)} query={q}"
        )
        for item in results:
            link = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("content", "")).strip()
            d = domain(link)
            if not d or not title:
                stats["invalid_url"] += 1
                continue
            if d in seen:
                stats["duplicate_in_run"] += 1
                continue
            if d in existing_domains:
                stats["already_stored_domain"] += 1
                continue
            if looks_like_reject(title, link, snippet, query=q):
                stats["filtered_not_target"] += 1
                continue

            seen.add(d)
            text = f"{title} {snippet}"
            contacts = extract_contacts(link)
            product = infer_product(text)
            rows.append({
                "company_name": title[:200], "website": link, "country": TARGET_COUNTRY, "industry": product,
                "buyer_type": "Potential buyer / industrial consumer", "product_interest": product,
                "contact_person": "", "email": contacts["email"], "whatsapp": contacts["whatsapp"], "phone": contacts["phone"],
                "linkedin": link if "linkedin.com" in link else "", "source": source, "evidence": snippet[:500],
                "lead_score": "1" if contacts["email"] or contacts["phone"] or contacts["whatsapp"] else "0",
                "run_id": RUN_ID, "collected_at": COLLECTED_AT, "search_query": q,
            })
            stats["accepted"] += 1
            if len(rows) >= TARGET_LEADS_PER_RUN:
                print("LEAD_FILTER_AUDIT " + json.dumps(stats, ensure_ascii=False))
                print(f"LEAD_COLLECTION_TOTAL accepted={len(rows)} target={TARGET_LEADS_PER_RUN}")
                return rows
        print("LEAD_FILTER_AUDIT " + json.dumps(stats, ensure_ascii=False))
    print(f"LEAD_COLLECTION_TOTAL accepted={len(rows)} target={TARGET_LEADS_PER_RUN}")
    return rows

def main():
    rows = collect()
    existing_rows = []
    if OUTPUT.exists():
        with OUTPUT.open(encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))
    existing_keys = {domain(r.get("website", "")) for r in existing_rows if domain(r.get("website", ""))}
    new_rows = [r for r in rows if domain(r.get("website", "")) not in existing_keys]
    all_rows = existing_rows + new_rows
    # Preserve columns added by social_collector.py when rewriting the shared CSV.
    fieldnames = list(FIELDS)
    for row in all_rows:
        for field in row.keys():
            if field not in fieldnames:
                fieldnames.append(field)
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Collected {len(new_rows)} fresh leads in China focus; total stored: {len(all_rows)}")


if __name__ == "__main__":
    main()
