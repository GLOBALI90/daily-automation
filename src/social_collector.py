import csv, json, os, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEADS = DATA / "leads.csv"
PERSONAL_PROFILES = DATA / "personal_profiles.csv"
PERSONAL_PROFILE_FIELDS = ["platform", "public_name", "profile_url", "username", "run_id", "collected_at"]
DATA.mkdir(exist_ok=True)

# Product-first lead discovery. Defaults preserve the existing lubricant/China pilot.
PRODUCT_NAME = os.getenv("PRODUCT_NAME", "industrial lubricants").strip() or "industrial lubricants"
TARGET_MARKET = os.getenv("TARGET_MARKET", "China").strip() or "China"
BUYER_TYPES = os.getenv(
    "BUYER_TYPES", "manufacturers, importers, distributors, industrial end-users"
).strip() or "manufacturers, importers, distributors, industrial end-users"
MAX_ITEMS = min(5, max(1, int(os.getenv("SOCIAL_MAX_ITEMS", "3") or "3")))
REQUEST_INTERVAL_SECONDS = max(11.0, float(os.getenv("SOCIAL_REQUEST_INTERVAL_SECONDS", "15") or "15"))
LAST_PROFILE_REQUEST_AT = 0.0
LAST_CLOUDFLARE_REQUEST_AT = 0.0
CLOUDFLARE_SEARCH_USED_THIS_RUN = False
FACEBOOK_GOOGLE_SEARCH_USED_THIS_RUN = False
CLOUDFLARE_CALLS_THIS_RUN = 0
MAX_CLOUDFLARE_CALLS_PER_RUN = 2
CLOUDFLARE_AUDIT = []
PROFILE_AUDIT = []
DISCOVERY_COUNTS = {}
CLOUDFLARE_PRIMARY_RESULTS = {"linkedin": [], "facebook": []}
COMPOSIO_PRIMARY_RESULTS = {"linkedin": [], "facebook": []}
COMPOSIO_AUDIT = []
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "").strip()
COMPOSIO_FIRECRAWL_CONNECTED_ACCOUNT_ID = os.getenv("COMPOSIO_FIRECRAWL_CONNECTED_ACCOUNT_ID", "").strip()
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID", "GLOBALI90").strip()
CLOUDFLARE_PRIMARY_ENRICHMENT = os.getenv("CLOUDFLARE_PRIMARY_ENRICHMENT", "false").strip().lower() in ("1", "true", "yes", "on")
COMPOSIO_EXECUTE_URL = "https://backend.composio.dev/api/v3.1/tools/execute/FIRECRAWL_SEARCH"
CLOUDFLARE_PRIMARY_QUERY = (
    f'"{PRODUCT_NAME}" {BUYER_TYPES} {TARGET_MARKET} company contact website '
    f'"{PRODUCT_NAME}" {TARGET_MARKET} supplier importer distributor'
)
EXISTING_SOCIAL_KEYS = set()
EXISTING_PERSONAL_KEYS = set()
DUPLICATE_PREFILTER_SKIPPED = 0
PLATFORM = os.getenv("SOCIAL_PLATFORM", "auto").strip().lower().lstrip(chr(92) + "/")

APIFY = os.getenv("APIFY_API_TOKEN", "").strip()
SCRAPECREATORS = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
# Legacy Brave adapter is disabled in the workflow; kept only for compatibility tests.
BRAVE_SEARCH_API_KEY = ""
EXA_API_KEY = os.getenv("EXA_API_KEY", "").strip()
EXA_USAGE_FILE = DATA / "exa_search_usage.json"
EXA_MAX_MONTHLY_CALLS = 20

CLOUDFLARE_URL = os.getenv(
    "SOCIAL_DEEP_BACKUP_URL",
    "https://patient-moon-b87e.mohamadsbrfit760li.workers.dev"
).strip().rstrip("/")
CLOUDFLARE_TOKEN = os.getenv("SOCIAL_DEEP_BACKUP_TOKEN", "").strip()
RUN_ID = os.getenv(
    "GITHUB_RUN_ID",
    datetime.now(timezone.utc).strftime("social-%Y%m%d%H%M%S")
)
NOW = datetime.now(timezone.utc).isoformat()
RUN_STARTED_AT = NOW
REPORT_PATH = DATA / "social_run_report.json"

PLATFORMS = ["linkedin", "facebook"]
# AUTO rotates only the two requested platforms during focused collection.
AUTO_PLATFORMS = ["facebook", "linkedin"]

ACTORS = {
    "linkedin": "harvestapi~linkedin-profile-scraper",
    "linkedin_company": "harvestapi~linkedin-company",
    "linkedin_search": "harvestapi~linkedin-company-search",
    "facebook": "apify~facebook-pages-scraper",
    "x": "apidojo~tweet-scraper",
}

FIELDS = [
    "company_name", "website", "country", "industry", "buyer_type",
    "product_interest", "contact_person", "email", "whatsapp", "phone",
    "linkedin", "source", "evidence", "lead_score", "run_id", "collected_at",
    "search_query", "social_url", "platform", "username", "followers",
    "verified", "social_bio", "source_provider", "discovered_url",
    "profile_url_submitted", "profile_url_returned", "profile_fetch_status",
    "profile_data_fields", "discovery_method"
]

session = requests.Session()
session.headers.update({"User-Agent": "rozhanglobal-social-collector/1.0"})

# LinkedIn candidates come from the LinkedIn-specific provider search endpoint.
# Profile enrichment uses the configured Cloudflare/provider fallback chain.
LINKEDIN_SEARCH_RESULTS = {}


def val(x):
    if x is None:
        return ""
    if isinstance(x, (dict, list)):
        return json.dumps(x, ensure_ascii=False)
    return str(x).strip()


def domain(u):
    try:
        return urlparse(u).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def handle_from_url(u):
    try:
        parts = [p for p in urlparse(u).path.split("/") if p]
        return parts[1] if len(parts) > 1 and parts[0].lower() in {
            "in", "user", "users", "profile"
        } else (parts[0] if parts else "")
    except Exception:
        return ""


def canonical_linkedin_url(url):
    """Normalize localized LinkedIn hostnames before sending URLs to providers."""
    parsed = urlparse(val(url))
    host = (parsed.hostname or "").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        path = parsed.path.rstrip("/")
        return f"https://www.linkedin.com{path}/" if path else "https://www.linkedin.com/"
    return val(url)


def canonical_social_key(platform, url):
    value = val(url).split("#", 1)[0].rstrip("/")
    if platform == "linkedin" and value:
        value = canonical_linkedin_url(value).rstrip("/")
    return value


def is_existing_candidate(platform, url):
    key = canonical_social_key(platform, url)
    if not key:
        return False
    if platform == "linkedin" and urlparse(key).path.lower().startswith("/in/"):
        return key in EXISTING_PERSONAL_KEYS
    return (platform, key) in EXISTING_SOCIAL_KEYS


def good(p, u):
    d = domain(u)
    allowed = {
        "linkedin": ("linkedin.com",),
        "facebook": ("facebook.com",),
    }.get(p, ())
    return any(d == host or d.endswith("." + host) for host in allowed) and not any(
        x in u.lower()
        for x in ["/search", "/explore", "/hashtag", "/jobs"]
    )

# Strict pilot target: manufacturers and producers of industrial lubricants in China.
# No automotive parts, generic chemicals, consumer products, or unrelated lead collection.
INDUSTRY_TERMS = (
    "industrial lubricant", "industrial lubricants", "lubricant manufacturer",
    "lubricants manufacturer", "lubricating oil", "lubricating oils",
    "lubricating grease", "industrial grease", "grease manufacturer",
    "metalworking fluid", "hydraulic oil", "gear oil", "compressor oil",
    "turbine oil", "industrial oil manufacturer", "lubricant blending",
    "润滑油", "工业润滑油", "润滑脂", "工业润滑脂", "润滑剂",
    "润滑油厂家", "润滑油生产", "润滑油制造商", "润滑脂生产厂家"
)
# Keep the detailed legacy synonym set for the default pilot; arbitrary products
# use their supplied phrase and meaningful tokens instead of lubricant-only gates.
if PRODUCT_NAME.lower() in ("industrial lubricants", "industrial lubricant", "lubricating oil"):
    PRODUCT_TERMS = INDUSTRY_TERMS
else:
    _product_tokens = [token for token in re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", PRODUCT_NAME.lower()) if len(token) > 2]
    PRODUCT_TERMS = tuple(dict.fromkeys([PRODUCT_NAME.lower(), *_product_tokens]))
COMPANY_TERMS = (
    "company", "group", "limited", "ltd", "manufacturer", "manufacturing",
    "factory", "supplier", "production", "importer", "exporter", "refinery",
    "plant", "materials", "chemical industry", "industrial"
)
BUYER_ROLE_TERMS = (
    "procurement", "purchasing", "purchase manager", "buyer", "sourcing",
    "supply chain", "import manager", "procurement manager", "purchasing manager",
    "commercial manager", "trading", "raw material buyer", "采购", "采购经理"
)
DECISION_MAKER_TERMS = (
    "owner", "founder", "co-founder", "ceo", "president", "director",
    "general manager", "managing director", "export manager", "sales manager",
    "business development", "commercial director", "总经理", "董事长",
    "创始人", "销售经理", "出口经理", "业务发展"
)
CHINA_SIGNALS = (
    "china", "chinese", "jiangsu", "guangdong", "zhejiang", "shandong",
    "shanghai", "tianjin", "hebei", "liaoning", "fujian", "hubei",
    "suzhou", "nanjing", "wuxi", "changzhou", "nantong", "guangzhou",
    "shenzhen", "foshan", "dongguan", "huizhou", "ningbo", "hangzhou",
    "qingdao", "dongying", "yantai", "weifang", "jinan", "tangshan",
    "cangzhou", "dalian", "shenyang", "yingkou", "xiamen", "quanzhou",
    "fuzhou", "wuhan", "yichang"
)
MARKET_SIGNALS = (
    CHINA_SIGNALS if TARGET_MARKET.lower() in ("china", "cn", "中国")
    else tuple(dict.fromkeys(
        [TARGET_MARKET.lower(), *[part for part in re.findall(r"[a-zA-Z0-9]+", TARGET_MARKET.lower()) if len(part) > 2]]
    ))
)
BUYER_TYPE_TERMS = tuple(
    dict.fromkeys(
        [part.strip().lower() for part in re.split(r"[,;|]", BUYER_TYPES) if part.strip()]
        + ["manufacturer", "manufacturing", "factory", "producer", "supplier", "importer", "distributor",
           "wholesaler", "industrial end-user", "procurement", "purchasing", "采购", "生产厂家"]
    )
)
PRIORITY_REGION_SIGNALS = ("jiangsu", "suzhou", "nanjing", "wuxi", "changzhou", "nantong")


def infer_social_industry(company, website, location, bio):
    text = " ".join([company, website, location, bio]).lower()
    if any(term in text for term in PRODUCT_TERMS):
        return PRODUCT_NAME
    return ""


def relevant_social_profile(platform, url, company, website, location, bio):
    """Accept product-relevant public social pages in the configured target market."""
    text = " ".join([url, company, website, location, bio]).lower()
    location_text = val(location).lower()
    if not company or company.lower() in {"unknown", "none", "n/a", "facebook", "linkedin"}:
        return False
    # An explicit non-China location overrides a Chinese keyword in a search URL
    # or company slug (for example, /jiangsu-... whose profile says India).
    non_china_locations = (
        "india", "indian", "south korea", "korea", "japan", "united states",
        "usa", "united kingdom", "england", "germany", "france", "italy",
        "spain", "brazil", "indonesia", "thailand", "vietnam", "malaysia",
        "singapore", "australia", "canada", "pakistan", "bangladesh",
        "saudi arabia", "united arab emirates", "turkey", "mexico",
    )
    if TARGET_MARKET.lower() in ("china", "cn", "中国") and any(signal in location_text for signal in non_china_locations):
        return False
    market_match = any(signal in text for signal in MARKET_SIGNALS)
    if TARGET_MARKET.lower() in ("china", "cn", "中国"):
        market_match = market_match or domain(url).endswith(".cn")
    if not market_match:
        return False
    product_match = any(term in text for term in PRODUCT_TERMS)
    buyer_match = any(term in text for term in BUYER_TYPE_TERMS)
    return product_match and buyer_match


NATIVE_QUERIES = {
    "linkedin": [
        f'{TARGET_MARKET} "{PRODUCT_NAME}" {BUYER_TYPES} company',
        f'{TARGET_MARKET} "{PRODUCT_NAME}" procurement purchasing sourcing',
        f'{TARGET_MARKET} "{PRODUCT_NAME}" supplier importer distributor',
    ],
    "facebook": [
        f'{TARGET_MARKET} "{PRODUCT_NAME}" {BUYER_TYPES}',
        f'{TARGET_MARKET} "{PRODUCT_NAME}" company website contact',
        f'{TARGET_MARKET} "{PRODUCT_NAME}" supplier importer distributor',
    ],
}

DISCOVERY_METHODS = {
    "linkedin": "apify_linkedin_company_search",
    "facebook": "cloudflare_facebook_public_page_search",
}


def _scrapecreators_get(path, params):
    if not SCRAPECREATORS:
        raise RuntimeError("SCRAPECREATORS_API_KEY missing for native social discovery")
    r = session.get(
        "https://api.scrapecreators.com" + path,
        headers={"x-api-key": SCRAPECREATORS, "Accept": "application/json"},
        params=params,
        timeout=45,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError("ScrapeCreators native discovery returned success=false")
    return data


def _facebook_native_candidates(query):
    # First search the public Facebook Ad Library company/page index.
    data = _scrapecreators_get(
        "/v1/facebook/adLibrary/search/companies", {"query": query}
    )
    items = data.get("searchResults") or data.get("results") or []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        alias = val(item.get("page_alias") or item.get("pageAlias") or item.get("username"))
        page_id = val(item.get("page_id") or item.get("pageId") or item.get("id"))
        url = val(item.get("url") or item.get("page_url") or item.get("page_profile_uri"))
        if not url and alias:
            url = f"https://www.facebook.com/{alias}/"
        if not url and page_id:
            url = f"https://www.facebook.com/{page_id}"
        if not url:
            continue
        out.append({
            "url": url,
            "title": val(item.get("page_name") or item.get("name") or alias),
            # Never inject the search query into evidence: it can make an
            # unrelated page appear to match China/industry filters.
            "content": val(item.get("description") or item.get("about") or
                           item.get("category") or item.get("category_name")),
        })
    if out:
        return out

    # Ad Library company search is not a general Facebook Page search. If it
    # has no match, make one bounded Google-index fallback per run (1 credit),
    # restricted to public Facebook URLs. The query itself is never evidence.
    global FACEBOOK_GOOGLE_SEARCH_USED_THIS_RUN
    if FACEBOOK_GOOGLE_SEARCH_USED_THIS_RUN:
        return []
    FACEBOOK_GOOGLE_SEARCH_USED_THIS_RUN = True
    google_query = (
        f'site:facebook.com "{PRODUCT_NAME}" "{TARGET_MARKET}" '
        f'({BUYER_TYPES}) company website contact'
    )
    google_data = _scrapecreators_get(
        "/v1/google/search", {"query": google_query, "region": "CN", "page": 1}
    )
    google_items = google_data.get("results") or []
    for item in google_items:
        if not isinstance(item, dict):
            continue
        url = val(item.get("url"))
        if not url or not good("facebook", url):
            continue
        if any(part in urlparse(url).path.lower() for part in (
            "/posts/", "/photos/", "/videos/", "/reel/", "/reels/",
            "/watch", "/groups/", "/events/", "/marketplace/", "/story.php",
            "/permalink.php", "/login"
        )):
            continue
        out.append({
            "url": url,
            "title": val(item.get("title")),
            "content": val(item.get("description") or item.get("snippet")),
            "search_provider": "ScrapeCreators Google Search",
        })
    print(
        f"Facebook public page search fallback: google_used=true; "
        f"results={len(google_items)}; facebook_candidates={len(out)}"
    )
    return out


def _x_native_candidates(query):
    if not APIFY:
        raise RuntimeError("APIFY_API_TOKEN missing for X native search")
    # Search public X posts and extract author profile URLs from those results.
    items = actor("x", {"searchTerms": [query], "maxItems": 50, "sort": "Latest"})
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        author = item.get("author") or item.get("user") or {}
        if not isinstance(author, dict):
            author = {}
        core = (((item.get("core") or {}).get("user_results") or {}).get("result") or {})
        legacy = core.get("legacy") if isinstance(core, dict) else {}
        if not isinstance(legacy, dict):
            legacy = {}
        username = val(
            author.get("userName") or author.get("username") or author.get("screen_name")
            or item.get("authorUsername") or item.get("username")
            or legacy.get("screen_name")
        )
        url = val(
            author.get("url") or author.get("profileUrl") or author.get("profile_url")
            or item.get("authorUrl") or item.get("profileUrl")
        )
        if not url and username:
            url = f"https://x.com/{username}"
        if not url:
            continue
        out.append({
            "url": url,
            "title": val(author.get("name") or item.get("authorName") or username),
            "content": " ".join([
                val(item.get("text") or item.get("fullText") or item.get("tweetText") or legacy.get("description")),
                query,
            ]),
        })
    return out


def _decodo_google_search(query):
    """Use the configured Decodo search API for indexed search results only."""
    key = os.getenv("DECODO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DECODO_API_KEY missing")
    auth = key if key.lower().startswith("basic ") else "Basic " + key
    response = session.post(
        "https://scraper-api.decodo.com/v2/scrape",
        headers={
            "Authorization": auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "target": "google_search",
            "query": query,
            "geo": "China",
            "parse": True,
            "headless": "html",
        },
        timeout=18,
    )
    response.raise_for_status()
    payload = response.json()

    def find_organic(node):
        if isinstance(node, dict):
            organic = node.get("organic")
            if isinstance(organic, list):
                return organic
            for value in node.values():
                found = find_organic(value)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = find_organic(value)
                if found is not None:
                    return found
        return None

    organic = find_organic(payload) or []
    return [{
        "url": item.get("url", ""),
        "title": item.get("title", ""),
        "content": item.get("desc", "") or item.get("snippet", ""),
    } for item in organic if isinstance(item, dict)]


def _linkedin_provider_candidates(query):
    """Use the LinkedIn-specific ScrapeCreators public-post search adapter.

    This avoids the generic You.com, Decodo Google Search, and SearXNG
    discovery chain. Only LinkedIn post authors are returned as candidates.
    """
    if not SCRAPECREATORS:
        raise RuntimeError("SCRAPECREATORS_API_KEY missing for LinkedIn provider search")
    response = session.get(
        "https://api.scrapecreators.com/v1/linkedin/search/posts",
        headers={"x-api-key": SCRAPECREATORS, "Accept": "application/json"},
        params={"query": query},
        timeout=20,
    )
    # ScrapeCreators currently uses HTTP 404 for some valid searches with no
    # matching public posts. Treat that as an empty result, not a broken endpoint.
    if getattr(response, "status_code", None) == 404:
        print("LinkedIn provider search: no public post matches (HTTP 404 interpreted as empty results)")
        return []
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError("ScrapeCreators LinkedIn search returned success=false")
    posts = payload.get("posts", []) if isinstance(payload, dict) else []
    out = []
    seen = set()
    for post in posts if isinstance(posts, list) else []:
        if not isinstance(post, dict):
            continue
        author = post.get("author") or {}
        if not isinstance(author, dict):
            author = {}
        url = val(author.get("url") or author.get("profileUrl")).split("?")[0].rstrip("/")
        if not url or "linkedin.com" not in domain(url).lower():
            continue
        url = canonical_linkedin_url(url).rstrip("/")
        path = urlparse(url).path.lower()
        # Accept public company pages and public personal profiles. Personal
        # profiles must pass a separate China + industry + decision-role gate.
        if not (path.startswith("/company/") or path.startswith("/in/")) or url in seen:
            continue
        seen.add(url)
        title = val(author.get("name") or post.get("authorName"))
        snippet = val(post.get("description") or post.get("text") or post.get("content"))
        headline = val(author.get("headline") or author.get("occupation") or author.get("title"))
        current_company = val(author.get("currentCompany") or author.get("company") or author.get("companyName"))
        location = val(author.get("location") or author.get("geoLocation"))
        LINKEDIN_SEARCH_RESULTS[url] = {
            "title": title, "snippet": snippet, "headline": headline,
            "current_company": current_company, "location": location,
            "search_provider": "ScrapeCreators LinkedIn Search",
        }
        out.append({
            "url": url,
            "title": title,
            "content": " ".join([title, snippet, headline, current_company, location]),
            "headline": headline, "current_company": current_company, "location": location,
            "search_provider": "ScrapeCreators LinkedIn Search",
        })
    print(f"LinkedIn provider search: results={len(posts)}; candidate_profiles={len(out)}")
    return out


def _reserve_exa_search_call():
    """Persist a conservative monthly request cap; failed attempts count too."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        usage = json.loads(EXA_USAGE_FILE.read_text(encoding="utf-8")) if EXA_USAGE_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        # Fail closed rather than risk unmetered API calls if quota state is unreadable.
        print("EXA_SEARCH_BACKUP blocked: monthly usage state unreadable")
        return False
    if usage.get("month") != month:
        usage = {"month": month, "calls": 0}
    calls = int(usage.get("calls", 0) or 0)
    if calls >= EXA_MAX_MONTHLY_CALLS:
        print(f"EXA_SEARCH_BACKUP blocked: monthly_cap={EXA_MAX_MONTHLY_CALLS}; month={month}")
        return False
    usage["calls"] = calls + 1
    EXA_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXA_USAGE_FILE.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _exa_search_candidates(platform, query):
    """Exa indexed web search backup; only public Facebook/LinkedIn pages are returned."""
    if not EXA_API_KEY or platform not in ("facebook", "linkedin"):
        return []
    if not _reserve_exa_search_call():
        return []

    if platform == "facebook":
        search_query = (
            'Public Facebook pages for China-based industrial lubricant or lubricating oil '
            'manufacturers and industrial grease factories; Chinese terms 润滑油 润滑脂 生产厂家'
        )
        include_domains = ["facebook.com"]
    else:
        search_query = (
            'Public LinkedIn company pages for China-based industrial lubricant or lubricating oil '
            'manufacturers and industrial grease factories; Chinese terms 润滑油 润滑脂 生产厂家'
        )
        include_domains = ["linkedin.com"]

    response = session.post(
        "https://api.exa.ai/search",
        headers={
            "x-api-key": EXA_API_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "query": search_query,
            "type": "auto",
            "numResults": 10,
            "includeDomains": include_domains,
            "contents": {"highlights": True},
        },
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("results") or []
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = val(item.get("url")).split("#", 1)[0].rstrip("/")
        if not url or not good(platform, url) or url in seen:
            continue
        path = urlparse(url).path.lower()
        if platform == "linkedin" and not path.startswith("/company/"):
            continue
        if platform == "facebook" and any(part in path for part in (
            "/posts/", "/photos/", "/videos/", "/reel/", "/reels/",
            "/watch", "/groups/", "/events/", "/marketplace/",
            "/story.php", "/permalink.php", "/login"
        )):
            continue
        highlights = item.get("highlights") or []
        content = " ".join(val(value) for value in highlights if val(value)) if isinstance(highlights, list) else val(highlights)
        content = content or val(item.get("text") or item.get("summary"))
        seen.add(url)
        out.append({
            "url": url,
            "title": val(item.get("title")),
            "content": content,
            "search_provider": "Exa Search API",
        })
    print(
        f"EXA_SEARCH_BACKUP platform={platform}; "
        f"results={len(items)}; usable_public_pages={len(out)}"
    )
    return out


def _brave_search_candidates(platform, query):
    """Independent indexed-search backup; the search query is never evidence."""
    if not BRAVE_SEARCH_API_KEY:
        return []

    if platform == "facebook":
        search_query = (
            'site:facebook.com ("industrial lubricant" OR "lubricating oil" OR '
            '"industrial grease" OR 润滑油 OR 润滑脂) '
            '(China OR Chinese OR 中国) (manufacturer OR factory OR 生产厂家)'
        )
    elif platform == "linkedin":
        search_query = (
            'site:linkedin.com/company/ ("industrial lubricant" OR '
            '"lubricating oil" OR "industrial grease" OR 润滑油 OR 润滑脂) '
            '(China OR Chinese OR 中国) (manufacturer OR factory OR 生产厂家)'
        )
    else:
        return []

    response = session.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
            "Accept": "application/json",
        },
        params={
            "q": search_query,
            "count": 10,
            "country": "CN",
            "search_lang": "en",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    web = payload.get("web") or {}
    items = web.get("results") or []
    out = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = val(item.get("url")).split("#", 1)[0].rstrip("/")
        if not url or not good(platform, url) or url in seen:
            continue
        path = urlparse(url).path.lower()
        if platform == "linkedin" and not path.startswith("/company/"):
            continue
        if platform == "facebook" and any(part in path for part in (
            "/posts/", "/photos/", "/videos/", "/reel/", "/reels/",
            "/watch", "/groups/", "/events/", "/marketplace/",
            "/story.php", "/permalink.php", "/login"
        )):
            continue
        seen.add(url)
        out.append({
            "url": url,
            "title": val(item.get("title")),
            "content": val(item.get("description") or item.get("snippet")),
            "search_provider": "Brave Search API",
        })
    print(
        f"BRAVE_SEARCH_BACKUP platform={platform}; "
        f"results={len(items)}; usable_public_pages={len(out)}"
    )
    return out


def _apify_linkedin_company_candidates(query):
    """Search public LinkedIn company pages through Apify, with bounded cost."""
    if not APIFY:
        raise RuntimeError("APIFY_API_TOKEN missing for LinkedIn company search")
    items = actor("linkedin_search", {
        "searchQuery": query,
        "maxItems": min(15, MAX_ITEMS * 3),
        "takePages": 1,
        "locations": ["China"],
        "maxTotalChargeUsd": 0.10,
    })
    out = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = val(
            item.get("linkedinUrl") or item.get("linkedin_url")
            or item.get("companyUrl") or item.get("url")
        ).split("?")[0].rstrip("/")
        if not url or not good("linkedin", url):
            continue
        url = canonical_linkedin_url(url).rstrip("/")
        if not urlparse(url).path.lower().startswith("/company/") or url in seen:
            continue
        seen.add(url)
        title = val(item.get("name") or item.get("companyName") or item.get("title"))
        content = " | ".join(val(v) for v in (
            item.get("description"), item.get("industry"), item.get("location"),
            item.get("headquarters"), item.get("website"), item.get("specialties")
        ) if val(v))
        result = {
            "url": url,
            "title": title,
            "content": content,
            "search_provider": "Apify LinkedIn Company Search",
        }
        LINKEDIN_SEARCH_RESULTS[url] = {
            "title": title, "snippet": content, "search_provider": result["search_provider"]
        }
        out.append(result)
    print(f"APIFY_LINKEDIN_SEARCH results={len(items) if isinstance(items, list) else 0}; public_company_candidates={len(out)}")
    return out


def native_candidates(platform, query):
    if platform == "facebook":
        # Facebook discovery is handled by Cloudflare, with Exa as a bounded backup.
        return []
    if platform == "linkedin":
        # LinkedIn company discovery runs only through Apify. Fail closed when
        # the token is missing; do not silently fall back to retired providers.
        if not APIFY:
            print("APIFY_LINKEDIN_SEARCH skipped: APIFY_API_TOKEN missing")
            return []
        return _apify_linkedin_company_candidates(query)
    raise ValueError(f"Unsupported social platform: {platform}")


def personal_profile_candidate_score(result, url):
    """Score public LinkedIn people only when China, industry and role evidence exists."""
    if not urlparse(url).path.lower().startswith("/in/"):
        return 0
    text = " ".join([
        val(result.get("title")), val(result.get("content")),
        val(result.get("headline")), val(result.get("current_company")),
        val(result.get("location")), url
    ]).lower()
    if not any(term in text for term in PRODUCT_TERMS):
        return 0
    if not any(term in text for term in MARKET_SIGNALS):
        return 0
    # A role mentioned only in a post can describe somebody else. Require the
    # buyer/decision-maker evidence in public author identity metadata instead.
    role_text = " ".join([
        val(result.get("title")), val(result.get("headline")),
        val(result.get("current_company"))
    ]).lower()
    if not any(term in role_text for term in BUYER_ROLE_TERMS + DECISION_MAKER_TERMS):
        return 0
    return 10 + sum(1 for term in MARKET_SIGNALS if term in text) + sum(
        1 for term in BUYER_ROLE_TERMS + DECISION_MAKER_TERMS if term in role_text
    )


def save_personal_profile(platform, url, public_name=""):
    """Persist only public account identity/provenance; never store contact details."""
    existing = []
    if PERSONAL_PROFILES.exists():
        with PERSONAL_PROFILES.open(encoding="utf-8", newline="") as handle:
            existing = list(csv.DictReader(handle))
    canonical = val(url).rstrip("/")
    if not canonical or any(
        row.get("platform") == platform and val(row.get("profile_url")).rstrip("/") == canonical
        for row in existing
    ):
        return False
    existing.append({
        "platform": platform,
        "public_name": val(public_name),
        "profile_url": canonical,
        "username": handle_from_url(canonical),
        "run_id": RUN_ID,
        "collected_at": NOW,
    })
    with PERSONAL_PROFILES.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PERSONAL_PROFILE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)
    return True


def social_candidate_score(platform, result, url):
    title = val(result.get("title"))
    snippet = val(result.get("content") or result.get("snippet") or result.get("description"))
    text = " ".join([title, snippet, url]).lower()
    if platform == "linkedin" and urlparse(url).path.lower().startswith("/in/"):
        return personal_profile_candidate_score(result, url)
    lubricant_hits = sum(1 for term in PRODUCT_TERMS if term in text)
    buyer_hits = sum(1 for term in BUYER_TYPE_TERMS if term in text)
    market_hits = sum(1 for term in MARKET_SIGNALS if term in text)
    # Require product, buyer-type and target-market evidence in the candidate itself.
    if not lubricant_hits or not buyer_hits or not market_hits:
        return 0
    return lubricant_hits * 5 + buyer_hits * 3 + market_hits * 2


def pace_cloudflare_request():
    global LAST_CLOUDFLARE_REQUEST_AT
    if LAST_CLOUDFLARE_REQUEST_AT:
        wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - LAST_CLOUDFLARE_REQUEST_AT)
        if wait > 0:
            print(f"CLOUDFLARE_REQUEST_PACING sleep_seconds={wait:.1f}")
            time.sleep(wait)
    LAST_CLOUDFLARE_REQUEST_AT = time.monotonic()


def cloudflare_search_candidates(platform, query):
    """Use at most one bounded Worker search per run to preserve one enrichment call."""
    global CLOUDFLARE_SEARCH_USED_THIS_RUN
    if CLOUDFLARE_SEARCH_USED_THIS_RUN:
        raise RuntimeError("cloudflare_search_once_per_run_reached")
    if not CLOUDFLARE_TOKEN:
        raise RuntimeError("SOCIAL_DEEP_BACKUP_TOKEN missing for discovery fallback")

    global CLOUDFLARE_CALLS_THIS_RUN
    if CLOUDFLARE_CALLS_THIS_RUN >= MAX_CLOUDFLARE_CALLS_PER_RUN:
        raise RuntimeError("cloudflare_per_run_cap_reached")

    # Search and profile collection share the same per-run Worker budget.
    CLOUDFLARE_CALLS_THIS_RUN += 1
    CLOUDFLARE_SEARCH_USED_THIS_RUN = True
    pace_cloudflare_request()
    started = time.monotonic()
    response = session.post(
        f"{CLOUDFLARE_URL}/search",
        headers={
            "Authorization": f"Bearer {CLOUDFLARE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"query": query, "platform": platform, "max_items": MAX_ITEMS},
        timeout=90,
    )
    if response.status_code >= 400:
        CLOUDFLARE_AUDIT.append({
            "route": "/search", "platform": platform, "http_status": response.status_code,
            "elapsed_seconds": round(time.monotonic() - started, 2), "outcome": "http_error"
        })
        raise RuntimeError(
            f"cloudflare_search_http_{response.status_code}:{response.text[:250]}"
        )

    payload = response.json()
    results = payload.get("results", []) if isinstance(payload, dict) else []
    candidates = []
    per_platform_counts = {"linkedin": 0, "facebook": 0}
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, dict):
            continue
        result_platform = val(result.get("platform")).lower()
        if result_platform not in PLATFORMS:
            continue
        if platform != "both" and result_platform != platform:
            continue
        urls = result.get("candidates", [])
        for url in urls if isinstance(urls, list) else []:
            if per_platform_counts[result_platform] >= MAX_ITEMS:
                break
            url = val(url)
            if url and good(result_platform, url):
                candidates.append({
                    "url": url,
                    "title": "",
                    "content": "",
                    "search_provider": "Cloudflare Browser Search",
                    "platform": result_platform,
                })
                per_platform_counts[result_platform] += 1
        if platform != "both" and per_platform_counts[result_platform] >= MAX_ITEMS:
            break

    CLOUDFLARE_AUDIT.append({
        "route": "/search", "platform": platform, "http_status": response.status_code,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "outcome": "success", "candidate_count": len(candidates)
    })
    print(
        f"Cloudflare discovery fallback: platform={platform}; http_status={response.status_code}; "
        f"elapsed_seconds={CLOUDFLARE_AUDIT[-1]['elapsed_seconds']}; candidates={len(candidates)}; "
        f"worker_actions={CLOUDFLARE_CALLS_THIS_RUN}/{MAX_CLOUDFLARE_CALLS_PER_RUN}"
    )
    return candidates


def _resolve_composio_firecrawl_account():
    """Resolve the real Composio REST account ID and owning user for Firecrawl."""
    if not COMPOSIO_API_KEY:
        raise RuntimeError("COMPOSIO_API_KEY missing")
    response = session.get(
        "https://backend.composio.dev/api/v3.1/connected_accounts",
        headers={"x-api-key": COMPOSIO_API_KEY, "Accept": "application/json"},
        params={"account_type": "ALL", "limit": 100},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    accounts = []
    if isinstance(data, list):
        accounts = data
    elif isinstance(data, dict):
        for key in ("items", "connected_accounts", "connectedAccounts", "results"):
            if isinstance(data.get(key), list):
                accounts = data[key]
                break
        if not accounts and isinstance(data.get("items"), dict):
            accounts = data["items"].get("data", []) or []

    active = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        toolkit = account.get("toolkit") or account.get("app") or {}
        slug = val(
            toolkit.get("slug") if isinstance(toolkit, dict) else toolkit
        ) or val(account.get("toolkit_slug") or account.get("app_slug"))
        status = val(account.get("status")).upper()
        if slug.lower() != "firecrawl" or status not in ("ACTIVE", "CONNECTED", "ENABLED"):
            continue
        account_id = val(account.get("id") or account.get("connected_account_id"))
        if not account_id:
            continue
        owner_id = val(
            account.get("user_id") or account.get("userId")
            or account.get("entity_id") or account.get("entityId")
        )
        active.append({
            "id": account_id,
            "user_id": owner_id,
            "is_default": bool(account.get("is_default") or account.get("isDefault")),
        })

    if not active:
        keys = sorted(payload.keys()) if isinstance(payload, dict) else []
        diagnostics = []
        for account in accounts[:10]:
            if not isinstance(account, dict):
                diagnostics.append({"type": type(account).__name__})
                continue
            toolkit = account.get("toolkit") or account.get("app") or {}
            diagnostics.append({
                "keys": sorted(account.keys()),
                "status": val(account.get("status") or (account.get("state") or {}).get("status") if isinstance(account.get("state"), dict) else account.get("status")),
                "toolkit": val(toolkit.get("slug") or toolkit.get("name") or toolkit.get("id")) if isinstance(toolkit, dict) else val(toolkit),
                "toolkit_slug": val(account.get("toolkit_slug") or account.get("app_slug") or account.get("appName")),
                "id_present": bool(account.get("id") or account.get("connected_account_id")),
                "owner_present": bool(account.get("user_id") or account.get("userId") or account.get("entity_id") or account.get("entityId")),
            })
        raise RuntimeError(
            "No active Firecrawl REST connected account found in this Composio API-key project "
            f"(total_items={payload.get('total_items', 'unknown') if isinstance(payload, dict) else 'unknown'}; "
            f"response_keys={keys}; account_diagnostics={json.dumps(diagnostics, ensure_ascii=False)})"
        )

    configured = COMPOSIO_FIRECRAWL_CONNECTED_ACCOUNT_ID
    selected = next((item for item in active if item["id"] == configured), None)
    if selected is None:
        selected = next((item for item in active if item["is_default"]), active[0])
        print(
            "COMPOSIO_ACCOUNT_ALIAS_NOT_REST_ID: using resolved active Firecrawl account; "
            f"active_accounts={len(active)}"
        )
    if not selected["user_id"]:
        selected["user_id"] = COMPOSIO_USER_ID
    return selected


def composio_search_candidates(platform, query):
    """Primary discovery via Composio's Firecrawl Search tool."""
    if not COMPOSIO_API_KEY:
        raise RuntimeError("COMPOSIO_API_KEY missing")
    if platform not in ("linkedin", "facebook"):
        return []

    domain_filter = "linkedin.com/company" if platform == "linkedin" else "facebook.com"
    search_query = (
        f'site:{domain_filter} "{PRODUCT_NAME}" "{TARGET_MARKET}" '
        f'({BUYER_TYPES}) company website contact'
    )
    # Resolve the actual REST connected-account ID and its owning user.
    # The MCP alias in GitHub Secrets is not necessarily the REST account ID.
    account = _resolve_composio_firecrawl_account()
    body = {
        "user_id": account["user_id"],
        "connected_account_id": account["id"],
        "arguments": {
            "q": search_query,
            "limit": min(10, max(3, MAX_ITEMS * 3)),
            "formats": ["markdown"],
        },
    }

    started = time.monotonic()
    response = session.post(
        COMPOSIO_EXECUTE_URL,
        headers={"x-api-key": COMPOSIO_API_KEY, "Accept": "application/json", "Content-Type": "application/json"},
        json=body,
        timeout=75,
    )
    if response.status_code >= 400:
        COMPOSIO_AUDIT.append({"platform": platform, "http_status": response.status_code, "outcome": "http_error"})
        raise RuntimeError(f"composio_firecrawl_http_{response.status_code}:{response.text[:250]}")
    payload = response.json()
    if isinstance(payload, dict) and (payload.get("successful") is False or payload.get("error")):
        raise RuntimeError("composio_firecrawl_tool_error:" + val(payload.get("error") or payload.get("message"))[:250])

    candidates, seen = [], set()
    def visit(node):
        if isinstance(node, dict):
            url = val(node.get("url") or node.get("link") or node.get("sourceURL"))
            title = val(node.get("title") or node.get("name"))
            content = val(node.get("markdown") or node.get("description") or node.get("snippet") or node.get("content") or node.get("text"))
            if url.startswith(("https://", "http://")) and url not in seen and good(platform, url):
                path = urlparse(url).path.lower()
                if platform != "linkedin" or path.startswith("/company/"):
                    if platform != "facebook" or not any(part in path for part in ("/posts/", "/photos/", "/videos/", "/reel/", "/watch", "/groups/", "/events/", "/marketplace/", "/login")):
                        seen.add(url)
                        candidates.append({
                            "url": url.split("#", 1)[0].rstrip("/"),
                            "title": title,
                            "content": content[:5000],
                            "search_provider": "Composio Firecrawl Search",
                            "platform": platform,
                        })
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(payload)
    COMPOSIO_AUDIT.append({
        "platform": platform, "http_status": response.status_code,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "outcome": "success", "candidate_count": len(candidates),
    })
    print(
        f"COMPOSIO_FIRECRAWL_PRIMARY platform={platform}; http_status={response.status_code}; "
        f"candidates={len(candidates)}; elapsed_seconds={COMPOSIO_AUDIT[-1]['elapsed_seconds']}"
    )
    return candidates


def discover(p):
    """Use Composio Firecrawl first, then bounded backup providers."""
    global DUPLICATE_PREFILTER_SKIPPED
    last_error = None
    ranked_by_url = {}
    personal_by_url = {}

    # Composio candidates have first priority; Cloudflare search results remain a backup.
    for item in COMPOSIO_PRIMARY_RESULTS.get(p, []):
        u = val(item.get("url")).rstrip("/")
        if not u or not good(p, u):
            continue
        if is_existing_candidate(p, u):
            DUPLICATE_PREFILTER_SKIPPED += 1
            print(f"Skipping previously stored Composio candidate before enrichment: platform={p}; url={u}")
            continue
        is_personal = p == "linkedin" and urlparse(u).path.lower().startswith("/in/")
        target = personal_by_url if is_personal else ranked_by_url
        target[u] = (1000, u, CLOUDFLARE_PRIMARY_QUERY, "composio_firecrawl_search_primary")

    # Cloudflare search is a secondary discovery source; enrichment remains the Worker stage.
    for item in CLOUDFLARE_PRIMARY_RESULTS.get(p, []):
        u = val(item.get("url")).rstrip("/")
        if not u or not good(p, u):
            continue
        if is_existing_candidate(p, u):
            DUPLICATE_PREFILTER_SKIPPED += 1
            print(f"Skipping previously stored candidate before enrichment: platform={p}; url={u}")
            continue
        is_personal = p == "linkedin" and urlparse(u).path.lower().startswith("/in/")
        target = personal_by_url if is_personal else ranked_by_url
        target[u] = (1000, u, CLOUDFLARE_PRIMARY_QUERY, "cloudflare_browser_search_primary")

    # Cloudflare remains primary. Use an independent web index only to
    # top up a short result set, before the existing social-specific providers.
    # Exa is a card-free monthly-credit backup. Usage is committed with run data;
    # fail closed at 100 requests/month to keep the free allowance conservative.
    if len(ranked_by_url) < MAX_ITEMS and EXA_API_KEY and p == "facebook":
        try:
            backup_results = _exa_search_candidates(p, NATIVE_QUERIES[p][0])
            for item in backup_results:
                u = val(item.get("url")).rstrip("/")
                if not u or not good(p, u) or is_existing_candidate(p, u):
                    continue
                score = social_candidate_score(p, item, u)
                if not score:
                    print(
                        f"Exa Search candidate rejected by strict qualification: "
                        f"platform={p}; url={u}"
                    )
                    continue
                candidate = (score, u, NATIVE_QUERIES[p][0], "exa_search_api_backup")
                current = ranked_by_url.get(u)
                if current is None or score > current[0]:
                    ranked_by_url[u] = candidate
        except Exception as exc:
            print(
                f"Exa Search backup failed: platform={p}; "
                f"error={type(exc).__name__}: {exc}"
            )

    for q in NATIVE_QUERIES[p]:
        if len(ranked_by_url) >= MAX_ITEMS and (
            p != "linkedin" or len(personal_by_url) >= 3
        ):
            break
        try:
            results = native_candidates(p, q)
        except Exception as exc:
            last_error = exc
            print(f"Native social discovery failed: platform={p}; method={DISCOVERY_METHODS[p]}; error={type(exc).__name__}: {exc}")
            continue

        for item in results:
            u = val(item.get("url")).rstrip("/")
            if not u or not good(p, u):
                continue
            if is_existing_candidate(p, u):
                DUPLICATE_PREFILTER_SKIPPED += 1
                print(f"Skipping previously stored candidate before enrichment: platform={p}; url={u}")
                continue
            score = social_candidate_score(p, item, u)
            if not score:
                continue
            is_personal = p == "linkedin" and urlparse(u).path.lower().startswith("/in/")
            target = personal_by_url if is_personal else ranked_by_url
            current = target.get(u)
            candidate = (score, u, q, DISCOVERY_METHODS[p])
            if current is None or score > current[0]:
                target[u] = candidate

        # Keep the company lead quota independent from account-only people.
        # Continue role-led LinkedIn searches until both buckets have useful
        # candidates or the bounded query list is exhausted.
        if len(ranked_by_url) >= MAX_ITEMS and (
            p != "linkedin" or len(personal_by_url) >= 3
        ):
            break

    ranked = sorted(ranked_by_url.values(), key=lambda item: item[0], reverse=True)
    personal_ranked = sorted(personal_by_url.values(), key=lambda item: item[0], reverse=True)

    # The Worker has a real /search route, separate from its /collect profile
    # route. Use it only when native discovery yields no relevant candidates.
    # Candidate URLs from this fallback are not trusted as leads until profile
    # enrichment and the strict China/manufacturer relevance check both pass.
    if len(ranked) < MAX_ITEMS and CLOUDFLARE_TOKEN and (p != "linkedin" or not APIFY) and not CLOUDFLARE_SEARCH_USED_THIS_RUN and CLOUDFLARE_CALLS_THIS_RUN < MAX_CLOUDFLARE_CALLS_PER_RUN:
        fallback_query = NATIVE_QUERIES[p][0]
        try:
            fallback_results = cloudflare_search_candidates(p, fallback_query)
            for item in fallback_results:
                url = val(item.get("url"))
                if url and good(p, url):
                    ranked_by_url.setdefault(
                        url,
                        (1, url, fallback_query, "cloudflare_browser_search_fallback"),
                    )
            ranked = sorted(ranked_by_url.values(), key=lambda item: item[0], reverse=True)
        except Exception as exc:
            print(
                f"Cloudflare discovery fallback failed: platform={p}; "
                f"error={type(exc).__name__}: {exc}"
            )

    # Gather a small candidate pool from each enabled platform. Final profile
    # validation remains strict; the main loop stops after five accepted leads.
    selected = ranked[:MAX_ITEMS]
    selected_people = personal_ranked[:3] if p == "linkedin" else []
    if selected or selected_people:
        method = (selected[0][3] if selected else selected_people[0][3])
        print(
            f"Native social discovery ranking: platform={p}; method={method}; "
            f"company_candidates={len(ranked)}; selected_for_enrichment={len(selected)}; "
            f"personal_account_candidates={len(selected_people)}; "
            f"top_score={(selected[0][0] if selected else selected_people[0][0])}"
        )
    elif last_error:
        print(f"Social discovery ended without candidates: platform={p}; last_error={type(last_error).__name__}: {last_error}")
    return [(u, q, method) for _, u, q, method in selected + selected_people]

def get_json(url, headers=None, params=None, timeout=45):
    r = session.get(
        url,
        headers=headers or {},
        params=params or {},
        timeout=timeout
    )
    try:
        r.raise_for_status()
    except requests.HTTPError as exc:
        detail = (getattr(r, "text", "") or "").replace("\n", " ")[:250]
        raise RuntimeError(f"provider_http_{r.status_code}:{detail}") from exc
    data = r.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError("provider_returned_success_false")
    return data


def scrapecreators_profile(p, url):
    if not SCRAPECREATORS:
        raise RuntimeError("SCRAPECREATORS_API_KEY missing")

    headers = {"x-api-key": SCRAPECREATORS}

    if p == "linkedin":
        url = canonical_linkedin_url(url)
        endpoint = (
            "https://api.scrapecreators.com/v1/linkedin/company"
            if "/company/" in urlparse(url).path.lower()
            else "https://api.scrapecreators.com/v1/linkedin/profile"
        )
        return get_json(endpoint, headers=headers, params={"url": url})

    if p == "facebook":
        return get_json(
            "https://api.scrapecreators.com/v1/facebook/profile",
            headers=headers,
            params={"url": url, "cache_max_age": "7d"}
        )

    return get_json(
        "https://api.scrapecreators.com/v1/twitter/profile",
        headers=headers,
        params={"handle": handle_from_url(url), "cache_max_age": "7d"}
    )


def actor(p, payload):
    if not APIFY:
        raise RuntimeError("APIFY_API_TOKEN missing")

    r = session.post(
        f"https://api.apify.com/v2/acts/{ACTORS[p]}/run-sync-get-dataset-items",
        headers={
            "Authorization": f"Bearer {APIFY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=180
    )
    try:
        r.raise_for_status()
    except requests.HTTPError as exc:
        detail = (getattr(r, "text", "") or "").replace("\n", " ")[:250]
        raise RuntimeError(f"apify_http_{r.status_code}:{detail}") from exc
    data = r.json()
    return data if isinstance(data, list) else data.get("items", [])


def apify_profile(p, url):
    if p == "linkedin":
        url = canonical_linkedin_url(url)
        if "/company/" in urlparse(url).path.lower():
            # Company pages require HarvestAPI's company actor and its
            # documented "companies" input, not the person-profile actor.
            return actor("linkedin_company", {"companies": [url]})
        # Use the actor schema's explicit URL field instead of the mixed URL/identifier field.
        return actor(p, {
            "profileScraperMode": "Profile details no email ($4 per 1k)",
            "urls": [canonical_linkedin_url(url)]
        })
    if p == "facebook":
        return actor(p, {"startUrls": [{"url": url}]})
    return actor(p, {
        "startUrls": [url],
        "maxItems": 1,
        "sort": "Latest"
    })


def cloudflare_profile(p, url):
    if not CLOUDFLARE_TOKEN:
        raise RuntimeError("SOCIAL_DEEP_BACKUP_TOKEN missing")

    global CLOUDFLARE_CALLS_THIS_RUN
    if CLOUDFLARE_CALLS_THIS_RUN >= MAX_CLOUDFLARE_CALLS_PER_RUN:
        raise RuntimeError("cloudflare_per_run_cap_reached")
    # Count the request before sending it so a failed attempt still consumes
    # this run's one-call allowance.
    CLOUDFLARE_CALLS_THIS_RUN += 1
    pace_cloudflare_request()
    started = time.monotonic()
    r = session.post(
        f"{CLOUDFLARE_URL}/collect",
        headers={
            "Authorization": f"Bearer {CLOUDFLARE_TOKEN}",
            "Content-Type": "application/json"
        },
        json={"url": url, "platform": p, "max_items": 1},
        timeout=90
    )
    if r.status_code >= 400:
        CLOUDFLARE_AUDIT.append({
            "route": "/collect", "platform": p, "profile_url": url,
            "http_status": r.status_code, "elapsed_seconds": round(time.monotonic() - started, 2),
            "outcome": "http_error"
        })
        raise RuntimeError(
            f"cloudflare_http_{r.status_code}:{r.text[:300]}"
        )
    data = r.json()
    CLOUDFLARE_AUDIT.append({
        "route": "/collect", "platform": p, "profile_url": url,
        "http_status": r.status_code, "elapsed_seconds": round(time.monotonic() - started, 2),
        "outcome": "response_received",
        "response_keys": sorted(list(data.keys())) if isinstance(data, dict) else ["<non_object_json>"]
    })
    print(
        f"CLOUDFLARE_COLLECT_AUDIT platform={p}; http_status={r.status_code}; "
        f"elapsed_seconds={CLOUDFLARE_AUDIT[-1]['elapsed_seconds']}; "
        f"response_keys={','.join(CLOUDFLARE_AUDIT[-1]['response_keys'])}"
    )
    return data


def pace_profile_enrichment(platform):
    global LAST_PROFILE_REQUEST_AT
    if LAST_PROFILE_REQUEST_AT:
        wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - LAST_PROFILE_REQUEST_AT)
        if wait > 0:
            print(f"SOCIAL_REQUEST_PACING sleep_seconds={wait:.1f}; platform={platform}")
            time.sleep(wait)
    LAST_PROFILE_REQUEST_AT = time.monotonic()


def provider_chain(p, url):
    errors = []

    # Cloudflare performs the first profile/contact enrichment attempt when enabled.
    # Apify is a backup for LinkedIn; the Worker action cap is always respected.
    if p not in ("linkedin", "facebook"):
        return "", None, [f"unsupported_platform:{p}"]

    providers = []
    if CLOUDFLARE_TOKEN and CLOUDFLARE_PRIMARY_ENRICHMENT:
        if CLOUDFLARE_CALLS_THIS_RUN < MAX_CLOUDFLARE_CALLS_PER_RUN:
            providers.append(("CloudflareDeepBackup", lambda: cloudflare_profile(p, url)))
        else:
            errors.append("CloudflareDeepBackup:cloudflare_per_run_cap_reached")
    elif p == "facebook" and CLOUDFLARE_TOKEN and CLOUDFLARE_CALLS_THIS_RUN < MAX_CLOUDFLARE_CALLS_PER_RUN:
        providers.append(("CloudflareDeepBackup", lambda: cloudflare_profile(p, url)))

    if p == "linkedin" and APIFY:
        providers.append(("Apify", lambda: apify_profile(p, url)))
    if not providers:
        errors.append("no_profile_provider_available_or_worker_cap_reached")
        return "", None, errors

    for name, fn in providers:
        try:
            data = fn()
            if not data:
                errors.append(f"{name}:empty")
                print(f"Social provider returned empty response: {name}")
                continue

            try:
                normalized = normalize_provider(p, name, data, url)
                company, web, loc, contact, email, phone, bio, followers, verified, _ = normalized
                extracted = profile_field_names(
                    company, web, loc, contact, email, phone, bio, followers, verified
                )
                has_substantive_detail = any((web, loc, email, phone, bio))
                if not company or len(extracted) < 2 or not has_substantive_detail:
                    errors.append(f"{name}:response_without_usable_profile_fields")
                    print(
                        f"Social provider response rejected as unusable: {name}; "
                        f"fields={json.dumps(extracted, ensure_ascii=False)}"
                    )
                    continue
                if not relevant_social_profile(
                    p, normalized[9] or url, company, web, loc, bio
                ):
                    errors.append(f"{name}:profile_not_relevant_to_target")
                    print(
                        f"Social provider response rejected as irrelevant: {name}; "
                        f"platform={p}; fields={json.dumps(extracted, ensure_ascii=False)}"
                    )
                    # A provider has already returned enough profile data to
                    # classify this URL as outside the pilot. Re-fetching the
                    # same URL through the metered Worker cannot make it relevant.
                    if name == "ScrapeCreators":
                        return "", None, errors
                    continue
            except Exception as exc:
                errors.append(f"{name}:normalization_error:{type(exc).__name__}")
                print(f"Social provider normalization failed: {name} | {type(exc).__name__}: {exc}")
                continue

            print(
                f"Social provider usable profile: {name}; "
                f"fields={json.dumps(extracted, ensure_ascii=False)}"
            )
            return name, data, errors

        except Exception as e:
            errors.append(f"{name}:{e}")
            print(f"Social provider failed: {name} | {e}")
            # Do not spend paid fallback quota to bypass an explicit Worker cap.
            error_text = str(e).lower()
            if name == "CloudflareDeepBackup" and (
                "cloudflare_http_429" in error_text
                or "daily limit" in error_text
                or "quota exceeded" in error_text
                or "limit reached" in error_text
            ):
                print("Cloudflare daily cap detected; skipping paid provider fallbacks.")
                return "", None, errors

    return "", None, errors


def returned_profile_url(provider, p, data):
    """Return only a URL explicitly present in the provider response; never the fallback."""
    x = data
    if provider == "CloudflareDeepBackup" and isinstance(x, dict):
        x = x.get("data") or x.get("result") or x.get("profile") or x
        if isinstance(x, dict) and isinstance(x.get("result"), dict):
            x = x["result"]
    if isinstance(x, list):
        x = x[0] if x else {}
    if not isinstance(x, dict):
        return ""
    candidates = [
        x.get("url"), x.get("input"), x.get("profileUrl"),
        x.get("profile_url"), x.get("pageUrl"), x.get("page_url"),
        x.get("facebookUrl") if p == "facebook" else None,
    ]
    # Common nested wrappers used by profile APIs.
    for key in ("data", "result", "profile", "page"):
        nested = x.get(key)
        if isinstance(nested, dict):
            candidates.extend([
                nested.get("url"), nested.get("input"), nested.get("profileUrl"),
                nested.get("profile_url"), nested.get("pageUrl"), nested.get("page_url")
            ])
    for candidate in candidates:
        value = val(candidate)
        if value.startswith(("https://", "http://")):
            return value
    return ""


def profile_field_names(company, web, loc, contact, email, phone, bio, followers, verified):
    # Count distinct profile attributes only; name and contact often contain the same value.
    values = {
        "name": company, "website": web, "location_or_address": loc,
        "email": email, "phone": phone, "description_or_bio": bio,
        "followers": followers, "verified": verified,
    }
    return [key for key, value in values.items() if val(value)]


def extract_public_contacts(text, email="", phone=""):
    """Extract only visibly published email/phone details from a public profile bio."""
    source = val(text)
    email_value = val(email)
    if not re.search(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", email_value, re.I):
        match = re.search(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", source)
        email_value = match.group(0).rstrip(".,;:") if match else ""

    # Require a nearby label for WhatsApp so IDs, follower counts, and unrelated
    # numbers are not accidentally treated as consent-ready contact details.
    wa_match = re.search(
        r"(?i)(?:whats\s?app|wa\.me|whatsapp number|whatsapp contact)\s*[:：\-]?\s*(\+?\d[\d ().\-]{7,}\d)",
        source,
    )
    whatsapp = re.sub(r"[\s().-]", "", wa_match.group(1)) if wa_match else ""
    if whatsapp.startswith("00"):
        whatsapp = "+" + whatsapp[2:]
    if whatsapp and not whatsapp.startswith("+"):
        whatsapp = "+" + whatsapp

    phone_value = val(phone)
    if not phone_value:
        phone_match = re.search(
            r"(?i)(?:phone|telephone|tel|mobile|contact number)\s*[:：\-]?\s*(\+?\d[\d ().\-]{7,}\d)",
            source,
        )
        phone_value = re.sub(r"\s+", " ", phone_match.group(1)).strip() if phone_match else ""
    if not phone_value and whatsapp:
        phone_value = whatsapp
    return email_value, whatsapp, phone_value


def _profile_dicts(value, depth=0):
    """Yield likely profile dictionaries from common API/Worker wrappers."""
    if depth > 6:
        return
    if isinstance(value, dict):
        priority = ("profile", "page", "business", "data", "result", "results", "items", "user", "account")
        for key in priority:
            child = value.get(key)
            if isinstance(child, (dict, list)):
                yield from _profile_dicts(child, depth + 1)
        yield value
        for key, child in value.items():
            if key not in priority and isinstance(child, (dict, list)):
                yield from _profile_dicts(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:10]:
            yield from _profile_dicts(child, depth + 1)


def _first_profile_value(nodes, aliases):
    for node in nodes:
        for key in aliases:
            value = node.get(key)
            if value not in (None, "", [], {}):
                if isinstance(value, dict):
                    value = value.get("text") or value.get("value") or value.get("name") or ""
                if isinstance(value, list):
                    value = ", ".join(val(v) for v in value if val(v))
                result = val(value)
                if result:
                    return result
    return ""


def normalize_cloudflare_profile(platform, data, fallback):
    nodes = list(_profile_dicts(data))
    name = _first_profile_value(nodes, (
        "name", "pageName", "page_name", "displayName", "display_name",
        "title", "fullName", "full_name", "username", "handle"
    ))
    website = _first_profile_value(nodes, (
        "website", "websiteUrl", "website_url", "externalUrl", "external_url",
        "link", "web_url"
    ))
    location = _first_profile_value(nodes, (
        "address", "location", "city", "currentCity", "current_city"
    ))
    email = _first_profile_value(nodes, (
        "email", "emailAddress", "email_address", "publicEmail",
        "public_email", "contactEmail", "contact_email"
    ))
    phone = _first_profile_value(nodes, (
        "phone", "phoneNumber", "phone_number", "contactPhone", "contact_phone"
    ))
    bio = _first_profile_value(nodes, (
        "description", "about", "intro", "bio", "biography", "pageIntro",
        "page_intro", "headline", "category", "services"
    ))
    followers = _first_profile_value(nodes, (
        "followersCount", "followers_count", "followerCount", "follower_count",
        "followers", "followersText", "likesCount", "likes_count", "fan_count",
        "fanCount", "likes"
    ))
    verified = _first_profile_value(nodes, ("verified", "isVerified", "is_verified"))
    profile_url = _first_profile_value(nodes, (
        "url", "profileUrl", "profile_url", "pageUrl", "page_url", "input"
    )) or fallback
    contact = name
    return name, website, location, contact, email, phone, bio, followers, verified, profile_url


def normalize_provider(p, provider, data, fallback):
    if provider == "CloudflareDeepBackup":
        return normalize_cloudflare_profile(p, data, fallback)

    if provider == "CloudflareDeepBackup" and isinstance(data, dict):
        # Workers may wrap profile data in one of these common response fields.
        data = data.get("data") or data.get("result") or data.get("profile") or data
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            data = data["result"]

    x = data[0] if isinstance(data, list) and data else data
    if not isinstance(x, dict):
        x = {}

    if provider == "ScrapeCreators":
        if p == "linkedin":
            is_company = "/company/" in urlparse(fallback).path.lower()
            if is_company:
                location = (
                    x.get("headquarters") or x.get("headquarter")
                    or x.get("location") or x.get("address") or ""
                )
                bio = " | ".join(
                    val(v) for v in [
                        x.get("description"), x.get("about"), x.get("industry"),
                        x.get("specialties"), x.get("slogan")
                    ] if val(v)
                )
                return (
                    val(x.get("name") or x.get("companyName")), 
                    val(x.get("website") or x.get("websiteUrl")), val(location),
                    val(x.get("name") or x.get("companyName")), 
                    val(x.get("email")), val(x.get("phone")), bio,
                    val(x.get("followers") or x.get("followerCount") or x.get("followersCount")),
                    val(x.get("verified")), val(x.get("url") or x.get("linkedinUrl")) or fallback
                )
            return (
                val(x.get("name")), val(x.get("website") or x.get("websiteUrl")), val(x.get("location")),
                val(x.get("name")), "", "", val(x.get("about") or x.get("headline")),
                val(x.get("followers") or x.get("followerCount")), val(x.get("verified")), fallback
            )
        if p == "facebook":
            return (
                val(x.get("name")), val(x.get("website")), val(x.get("address")),
                val(x.get("name")), val(x.get("email")), val(x.get("phone")),
                val(x.get("description") or x.get("services")),
                val(x.get("followerCount") or x.get("likeCount")),
                val(x.get("verified")), fallback
            )
        legacy = x.get("legacy") or {}
        return (
            val(legacy.get("name") or legacy.get("screen_name")),
            val(legacy.get("url")), val(legacy.get("location")),
            val(legacy.get("name") or legacy.get("screen_name")),
            "", "", val(legacy.get("description")),
            val(legacy.get("followers_count")),
            val(x.get("verified") or x.get("is_blue_verified")), fallback
        )


    if p == "linkedin" and "/company/" in urlparse(fallback).path.lower():
        location = (
            x.get("headquarters") or x.get("headquarter")
            or x.get("location") or x.get("address") or ""
        )
        bio = " | ".join(
            val(v) for v in [
                x.get("description"), x.get("about"), x.get("industry"),
                x.get("specialties"), x.get("tagline"), x.get("slogan")
            ] if val(v)
        )
        return (
            val(x.get("name") or x.get("companyName") or x.get("company_name")),
            val(x.get("website") or x.get("websiteUrl")), val(location),
            val(x.get("name") or x.get("companyName") or x.get("company_name")),
            val(x.get("email")), val(x.get("phone")), bio,
            val(x.get("followersCount") or x.get("followers") or x.get("followerCount")),
            val(x.get("verified")), val(x.get("url") or x.get("linkedinUrl") or x.get("companyUrl")) or fallback
        )

    if p == "linkedin":
        name = " ".join(
            z for z in [val(x.get("firstName")), val(x.get("lastName"))] if z
        )
        cp = x.get("currentPosition") or []
        company = (
            val(cp[0].get("companyName"))
            if cp and isinstance(cp[0], dict) else ""
        )
        loc = val(((x.get("location") or {}).get("parsed") or {}).get("text"))
        bio = " | ".join(
            z for z in [val(x.get("headline")), val(x.get("about")), company, loc]
            if z
        )
        return (
            company or name, val(x.get("website")), loc, name,
            "", "", bio, val(x.get("followerCount")),
            val(x.get("verified")), val(x.get("linkedinUrl")) or fallback
        )


    if p == "facebook":
        bio = val(x.get("description") or x.get("about"))
        return (
            val(x.get("name") or x.get("title")), val(x.get("website")),
            val(x.get("address")), val(x.get("name") or x.get("title")),
            val(x.get("email")), val(x.get("phone")), bio,
            val(x.get("followersCount") or x.get("followersText") or x.get("likesCount")),
            val(x.get("verified")), val(x.get("url") or x.get("input")) or fallback
        )

    return (
        val(x.get("author") or x.get("authorName") or x.get("username") or x.get("userName")),
        "", val(x.get("location")),
        val(x.get("author") or x.get("authorName") or x.get("username") or x.get("userName")),
        val(x.get("email")), val(x.get("phone")),
        val(x.get("authorDescription") or x.get("bio")),
        val(x.get("authorFollowers") or x.get("followersCount")),
        val(x.get("verified")),
        val(x.get("authorUrl") or x.get("profileUrl") or x.get("url")) or fallback
    )


def discover_with_auto_fallback(platform, is_auto):
    """Discover on the selected platform, then try the other allowed platform in auto mode."""
    found = discover(platform)
    if found or not is_auto:
        return platform, found
    alternate = "linkedin" if platform == "facebook" else "facebook"
    print(f"Auto fallback: {platform} returned no candidates; trying {alternate}.")
    return alternate, discover(alternate)


def write_run_report(platforms, discovered, added, provider_counts, outcome):
    report = {
        "run_id": RUN_ID,
        "started_at_utc": RUN_STARTED_AT,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "platforms": platforms,
        "max_valid_leads_per_run": MAX_ITEMS,
        "minimum_seconds_between_profile_enrichment_requests": REQUEST_INTERVAL_SECONDS,
        "discovered_candidates_total": discovered,
        "discovered_candidates_by_platform": DISCOVERY_COUNTS,
        "new_qualified_leads_added": added,
        "existing_social_records_checked": len(EXISTING_SOCIAL_KEYS),
        "existing_personal_profiles_checked": len(EXISTING_PERSONAL_KEYS),
        "duplicates_skipped_before_enrichment": DUPLICATE_PREFILTER_SKIPPED,
        "cloudflare_primary_discovery": bool(CLOUDFLARE_TOKEN),
        "provider_counts": provider_counts,
        "cloudflare": {
            "token_configured": bool(CLOUDFLARE_TOKEN),
            "calls_used": CLOUDFLARE_CALLS_THIS_RUN,
            "per_run_cap": MAX_CLOUDFLARE_CALLS_PER_RUN,
            "requests": CLOUDFLARE_AUDIT
        },
        "profile_fetch_results": PROFILE_AUDIT,
        "personal_profiles_saved_this_run": sum(
            1 for item in PROFILE_AUDIT if item.get("outcome") == "personal_account_saved"
        ),
        "notes": [
            "Public LinkedIn company pages, qualifying public LinkedIn personal profiles, and public Facebook pages only.",
            "HTTP success does not guarantee usable public profile fields.",
            "Outbound email and WhatsApp delivery are controlled by the workflow outreach_enabled input.",
            "Personal LinkedIn rows contain only public profile identity and provenance; no contact fields are stored."
        ]
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SOCIAL_RUN_REPORT " + json.dumps({
        "report_path": str(REPORT_PATH.relative_to(ROOT)),
        "outcome": outcome,
        "discovered_candidates": discovered,
        "new_qualified_leads_added": added,
        "composio_requests": COMPOSIO_AUDIT,
        "cloudflare_calls": CLOUDFLARE_CALLS_THIS_RUN,
        "cloudflare_requests": [
            {k: v for k, v in item.items() if k != "profile_url"}
            for item in CLOUDFLARE_AUDIT
        ]
    }, ensure_ascii=False), flush=True)


def main():
    # Always create the account-only CSV with a stable header, even when no
    # relevant personal profile is discovered in this run.
    if not PERSONAL_PROFILES.exists():
        with PERSONAL_PROFILES.open("w", encoding="utf-8", newline="") as handle:
            csv.DictWriter(handle, fieldnames=PERSONAL_PROFILE_FIELDS).writeheader()

    # Read durable state BEFORE discovery on every run. Known company pages and
    # previously saved personal accounts are excluded before profile enrichment.
    rows = []
    fields = []
    if LEADS.exists():
        with LEADS.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fields = list(rows[0].keys()) if rows else []
    for field in FIELDS:
        if field not in fields:
            fields.append(field)

    EXISTING_SOCIAL_KEYS.clear()
    for stored in rows:
        platform = val(stored.get("platform")).lower()
        if platform not in PLATFORMS:
            continue
        for field in ("social_url", "discovered_url", "linkedin"):
            url = val(stored.get(field))
            if url and good(platform, url):
                EXISTING_SOCIAL_KEYS.add((platform, canonical_social_key(platform, url)))

    EXISTING_PERSONAL_KEYS.clear()
    if PERSONAL_PROFILES.exists():
        with PERSONAL_PROFILES.open(encoding="utf-8", newline="") as handle:
            for stored in csv.DictReader(handle):
                url = val(stored.get("profile_url"))
                if url:
                    EXISTING_PERSONAL_KEYS.add(canonical_social_key(
                        val(stored.get("platform")).lower(), url
                    ))

    print(
        f"EXISTING_STATE_LOADED company_social_keys={len(EXISTING_SOCIAL_KEYS)}; "
        f"personal_profile_keys={len(EXISTING_PERSONAL_KEYS)}"
    )

    # Composio Firecrawl is the primary discovery stage for both requested networks.
    is_auto = PLATFORM not in PLATFORMS
    platforms_to_collect = AUTO_PLATFORMS if is_auto else [PLATFORM]
    for target in ("linkedin", "facebook"):
        COMPOSIO_PRIMARY_RESULTS[target] = []
        CLOUDFLARE_PRIMARY_RESULTS[target] = []

    if COMPOSIO_API_KEY:
        for target_platform in platforms_to_collect:
            try:
                COMPOSIO_PRIMARY_RESULTS[target_platform] = composio_search_candidates(
                    target_platform, CLOUDFLARE_PRIMARY_QUERY
                )
            except Exception as exc:
                print(
                    f"Composio primary discovery failed; continuing with bounded backups: "
                    f"platform={target_platform}; {type(exc).__name__}: {exc}"
                )
    else:
        print("COMPOSIO_PRIMARY_SKIPPED: COMPOSIO_API_KEY missing; bounded backups remain available")

    # Cloudflare /search is a bounded discovery backup only; /collect is used for profile/contact enrichment.
    if CLOUDFLARE_TOKEN and PLATFORM != "linkedin" and not any(COMPOSIO_PRIMARY_RESULTS.values()):
        try:
            primary_platform = "facebook" if is_auto else PLATFORM
            primary_candidates = cloudflare_search_candidates(
                primary_platform, CLOUDFLARE_PRIMARY_QUERY
            )
            for candidate in primary_candidates:
                candidate_platform = val(candidate.get("platform")).lower()
                if candidate_platform in CLOUDFLARE_PRIMARY_RESULTS:
                    CLOUDFLARE_PRIMARY_RESULTS[candidate_platform].append(candidate)
            print(
                "CLOUDFLARE_DISCOVERY_BACKUP "
                + json.dumps({
                    "requested_platform": primary_platform,
                    "linkedin_candidates": len(CLOUDFLARE_PRIMARY_RESULTS["linkedin"]),
                    "facebook_candidates": len(CLOUDFLARE_PRIMARY_RESULTS["facebook"]),
                }, ensure_ascii=False)
            )
        except Exception as exc:
            print(
                f"Cloudflare discovery backup failed; using other bounded providers: "
                f"{type(exc).__name__}: {exc}"
            )

    found = []
    for target_platform in platforms_to_collect:
        platform_candidates = discover(target_platform)
        DISCOVERY_COUNTS[target_platform] = len(platform_candidates)
        found.extend((target_platform, url, query, method)
                     for url, query, method in platform_candidates)
        print(
            f"SOCIAL_DISCOVERY_SUMMARY platform={target_platform}; "
            f"new_candidates_after_dedupe={len(platform_candidates)}"
        )

    if not found:
        print("No new Facebook or LinkedIn candidates found after checking stored records")
        write_run_report(
            platforms_to_collect, 0, 0,
            {"Apify": 0, "CloudflareDeepBackup": 0, "failed": 0},
            "no_new_candidates"
        )
        print(
            f"PERSONAL_PROFILE_SUMMARY discovered=0; new_account_only_records=0; "
            f"file={PERSONAL_PROFILES.relative_to(ROOT)}; contact_details_saved=false"
        )
        return

    # Backfill contact fields from already-stored public bios too. This matters
    # when a profile was collected before the extractor existed and is now deduped.
    contact_backfilled = 0
    for stored in rows:
        bio_text = stored.get("social_bio") or stored.get("evidence") or ""
        if not bio_text:
            continue
        email_value, whatsapp_value, phone_value = extract_public_contacts(
            bio_text, stored.get("email", ""), stored.get("phone", "")
        )
        changed = False
        for field, value in (
            ("email", email_value), ("whatsapp", whatsapp_value), ("phone", phone_value)
        ):
            if value and not (stored.get(field) or "").strip():
                stored[field] = value
                changed = True
        if changed:
            contact_backfilled += 1
    if contact_backfilled:
        print(f"PUBLIC_CONTACT_BACKFILL updated_existing_leads={contact_backfilled}")

    seen = {
        (r.get("platform", ""), r.get("social_url", "").rstrip("/"))
        for r in rows if r.get("social_url")
    }

    added = 0
    provider_counts = {
        "Apify": 0,
        "CloudflareDeepBackup": 0,
        "failed": 0
    }

    personal_profiles_added = 0
    for p, url, query, discovery_method in found:
        is_personal_linkedin = p == "linkedin" and urlparse(url).path.lower().startswith("/in/")
        if added >= MAX_ITEMS and not is_personal_linkedin:
            break
        if is_personal_linkedin:
            indexed = LINKEDIN_SEARCH_RESULTS.get(url.rstrip("/"), {})
            saved = save_personal_profile(p, url, indexed.get("title", ""))
            PROFILE_AUDIT.append({
                "platform": p,
                "profile_url": url,
                "outcome": "personal_account_saved" if saved else "personal_account_already_saved",
                "stored_fields": PERSONAL_PROFILE_FIELDS
            })
            if saved:
                personal_profiles_added += 1
            print(
                f"PERSONAL_PROFILE_ACCOUNT_ONLY platform=linkedin; saved={saved}; "
                f"profile_url={url}; contact_details_saved=false"
            )
            continue
        if (p, url.rstrip("/")) in seen:
            print(f"Skipping previously stored social profile: platform={p}; url={url}")
            continue
        indexed = LINKEDIN_SEARCH_RESULTS.get(url.rstrip("/"), {}) if p == "linkedin" else {}
        print(
            "SOCIAL_SOURCE_AUDIT " + json.dumps({
                "platform": p,
                "discovered_url": url,
                "profile_url_submitted": url,
                "search_query": query,
                "discovery_method": discovery_method,
                "search_provider": indexed.get("search_provider", ""),
                "note": "Public social data only; profile enrichment uses the free-first ScrapeCreators provider."
            }, ensure_ascii=False)
        )

        pace_profile_enrichment(p)
        provider, data, errors = provider_chain(p, url)
        if not provider:
            PROFILE_AUDIT.append({
                "platform": p, "profile_url": url, "outcome": "provider_failed",
                "errors": [str(error).split(":")[0] for error in errors]
            })
            provider_counts["failed"] += 1
            print("SOCIAL_FETCH_AUDIT " + json.dumps({
                "platform": p,
                "discovery_method": discovery_method,
                "discovered_url": url,
                "profile_url_submitted": url,
                "profile_url_returned": "",
                "provider": "",
                "status": "provider_failed",
                "profile_data_fields": [],
                "errors": errors
            }, ensure_ascii=False))
            print(f"All providers failed for {url}: " + " | ".join(errors))
            continue

        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        company, web, loc, contact, email, phone, bio, followers, verified, surl = (
            normalize_provider(p, provider, data, url)
        )
        returned_url = returned_profile_url(provider, p, data)
        extracted_fields = profile_field_names(
            company, web, loc, contact, email, phone, bio, followers, verified
        )
        fetch_status = (
            "profile_fields_extracted" if len(extracted_fields) >= 2
            else "response_without_usable_profile_fields"
        )
        profile_audit_row = {
            "platform": p, "profile_url": url, "provider": provider,
            "fetch_status": fetch_status, "extracted_fields": extracted_fields
        }
        PROFILE_AUDIT.append(profile_audit_row)
        print("SOCIAL_FETCH_AUDIT " + json.dumps({
            "platform": p,
            "discovery_method": discovery_method,
            "discovered_url": url,
            "profile_url_submitted": url,
            "profile_url_returned": returned_url,
            "provider": provider,
            "status": fetch_status,
            "profile_data_fields": extracted_fields,
            "returned_url_present": bool(returned_url)
        }, ensure_ascii=False))

        if not relevant_social_profile(p, surl or url, company, web, loc, bio):
            profile_audit_row["outcome"] = "rejected_not_relevant"
            print(f"Rejected low-confidence social result: platform={p}; discovered_url={url}; returned_profile_url={returned_url or 'NOT_RETURNED'}")
            continue
        profile_audit_row["outcome"] = "accepted_relevant"

        if (p, surl.rstrip("/")) in seen:
            continue

        email, whatsapp, phone = extract_public_contacts(bio, email, phone)
        print(
            f"PUBLIC_CONTACT_EXTRACTION platform={p}; "
            f"email_found={bool(email)}; whatsapp_found={bool(whatsapp)}; "
            f"phone_found={bool(phone)}"
        )

        row = {f: "" for f in fields}
        inferred_industry = infer_social_industry(company, web, loc, bio)
        product_labels = {
            "petroleum products": "petroleum products",
            "chemicals": "industrial chemicals",
            "petrochemicals": "petrochemicals and feedstocks",
            "steel": "steel and metal products",
            "renewable energy": "renewable-energy equipment and materials",
        }
        row.update({
            "company_name": company,
            "website": web,
            "country": TARGET_MARKET,
            "industry": inferred_industry,
            "buyer_type": BUYER_TYPES,
            "product_interest": product_labels.get(inferred_industry, inferred_industry),
            "contact_person": contact,
            "email": email,
            "whatsapp": whatsapp,
            "phone": phone,
            "linkedin": surl if p == "linkedin" else "",
            "source": f"social:{p}",
            "evidence": bio[:3000],
            "lead_score": str(min(
                100,
                25 + (20 if web else 0) + (25 if email else 0)
                + (10 if phone else 0) + (10 if bio else 0)
            )),
            "run_id": RUN_ID,
            "collected_at": NOW,
            "search_query": query,
            "discovery_method": discovery_method,
            "social_url": surl,
            "platform": p,
            "username": handle_from_url(surl),
            "followers": followers,
            "verified": verified,
            "social_bio": bio[:3000],
            "source_provider": provider,
            "discovered_url": url,
            "profile_url_submitted": url,
            "profile_url_returned": returned_url,
            "profile_fetch_status": fetch_status,
            "profile_data_fields": json.dumps(extracted_fields, ensure_ascii=False)
        })

        rows.append(row)
        seen.add((p, surl.rstrip("/")))
        added += 1

    with LEADS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(
        f"CLOUDFLARE_STATUS token_configured={bool(CLOUDFLARE_TOKEN)}; "
        f"calls={CLOUDFLARE_CALLS_THIS_RUN}/{MAX_CLOUDFLARE_CALLS_PER_RUN}; "
        "supported_platforms=linkedin,facebook; cloudflare_scope=facebook_only_when_apify_configured"
    )
    print(
        f"Social collection: platforms={','.join(platforms_to_collect)}; "
        f"discovered_candidates={len(found)}; added={added}; target_cap={MAX_ITEMS}; "
        f"cloudflare_calls={CLOUDFLARE_CALLS_THIS_RUN}/{MAX_CLOUDFLARE_CALLS_PER_RUN}; "
        f"providers={json.dumps(provider_counts, ensure_ascii=False)}"
    )
    write_run_report(platforms_to_collect, len(found), added, provider_counts, "completed")
    personal_discovered = sum(
        1 for p, u, _, _ in found
        if p == "linkedin" and urlparse(u).path.lower().startswith("/in/")
    )
    print(
        f"PERSONAL_PROFILE_SUMMARY discovered={personal_discovered}; "
        f"new_account_only_records={personal_profiles_added}; "
        f"file={PERSONAL_PROFILES.relative_to(ROOT)}; contact_details_saved=false"
    )


if __name__ == "__main__":
    main()
