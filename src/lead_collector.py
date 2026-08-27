import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
COMPANY = json.loads((ROOT / "config/company.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
OUTPUT = DATA / "leads.csv"

FALLBACK_QUERIES = [
    'China industrial buyer procurement chemicals petrochemicals company -jobs -careers -article -blog -directory',
    'China steel industrial consumer purchasing company -jobs -careers -article -blog -directory',
    'China petroleum products importer industrial consumer procurement company -jobs -careers -article -blog -directory',
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
    "linkedin", "source", "evidence", "lead_score", "run_id", "collected_at", "search_query"
]

HEADERS = {"User-Agent": "ROZHAN-Global-B2B-Research/1.0 (+https://www.rojanglobal.com)"}
SOCIAL_DOMAINS = {"linkedin.com", "facebook.com", "x.com", "twitter.com", "instagram.com"}
EXCLUDED_DOMAINS = {
    "indeed.com", "glassdoor.com", "ziprecruiter.com", "jobleads.com", "bebee.com",
    "michaelpage.com", "westlaketalent.com", "talents.vaia.com", "claytonpersonnel.com",
    "pointtobusinessservices.com", "pndatasol.com", "bluemailmedia.com", "datamarketersgroup.com",
    "thomasnet.com", "petrochemical.com", "lightsource.ai", "quora.com",
    "datacaptive.com", "averickmedia.com", "fountmedia.com", "bizinforusa.com",
    "tradewheel.com", "go4worldbusiness.com",
}
EXCLUDED_WORDS = {
    "job", "jobs", "career", "careers", "hiring", "vacancy", "vacancies", "employment",
    "recruit", "recruitment", "article", "blog", "guide", "directory", "list", "email list",
    "course", "webinar", "press release", "news", "magazine",
}
GEMINI_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
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


def looks_like_reject(title, url, snippet):
    d = domain(url)
    if not d or d in EXCLUDED_DOMAINS:
        return True
    text = f"{title} {url} {snippet}".lower()
    return any(word in text for word in EXCLUDED_WORDS)


def load_existing_domains():
    if not OUTPUT.exists():
        return set()
    try:
        with OUTPUT.open(encoding="utf-8") as f:
            return {domain(r.get("website", "")) for r in csv.DictReader(f) if domain(r.get("website", ""))}
    except Exception:
        return set()


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
        return FALLBACK_QUERIES

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
        r = requests.post(
            GEMINI_BASE.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": GEMINI_MODEL, "temperature": 0.3, "messages": [{"role": "user", "content": prompt}]},
            timeout=45,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1:
            queries = json.loads(text[start:end + 1])
            if isinstance(queries, list):
                queries = [str(q).strip() for q in queries if str(q).strip()]
                if len(queries) >= 3:
                    print(f"Gemini planned 3 search queries for sector: {sector} | China region: {region}")
                    return queries[:3]
    except Exception as exc:
        print(f"Gemini query planning failed: {exc}")
    return FALLBACK_QUERIES


def you_search(query, num=RESULTS_PER_QUERY):
    key = os.getenv("YDC_API_KEY")
    if not key:
        raise RuntimeError("YDC_API_KEY is missing")
    r = requests.get(
        "https://api.you.com/v1/search",
        params={"query": query, "count": min(num, 100)},
        headers={"X-API-Key": key, "Accept": "application/json"},
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


def search(query, num=RESULTS_PER_QUERY):
    try:
        results = you_search(query, num)
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
    queries = plan_queries(existing_domains)
    region, _ = pick_china_region()
    for q in queries:
        results, source = search(q, num=RESULTS_PER_QUERY)
        for item in results:
            link = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("content", "")).strip()
            d = domain(link)
            if not d or d in seen or d in existing_domains or looks_like_reject(title, link, snippet):
                continue
            seen.add(d)
            text = f"{title} {snippet}"
            contacts = extract_contacts(link)
            rows.append({
                "company_name": title[:200], "website": link, "country": TARGET_COUNTRY, "industry": infer_product(text),
                "buyer_type": "Potential buyer / industrial consumer", "product_interest": infer_product(text),
                "contact_person": "", "email": contacts["email"], "whatsapp": contacts["whatsapp"], "phone": contacts["phone"],
                "linkedin": link if "linkedin.com" in link else "", "source": source, "evidence": snippet[:500],
                "lead_score": "1" if contacts["email"] or contacts["phone"] or contacts["whatsapp"] else "0",
                "run_id": RUN_ID, "collected_at": COLLECTED_AT, "search_query": q,
            })
            if len(rows) >= TARGET_LEADS_PER_RUN:
                return rows
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
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(all_rows)
    print(f"Collected {len(new_rows)} fresh leads in China focus; total stored: {len(all_rows)}")


if __name__ == "__main__":
    main()
