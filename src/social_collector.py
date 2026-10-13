import csv, json, os, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEADS = DATA / "leads.csv"
DATA.mkdir(exist_ok=True)

# Up to five accepted leads per run. The Cloudflare Worker itself remains
# capped at one request per run to preserve its four-actions/day allowance.
MAX_ITEMS = min(5, max(1, int(os.getenv("SOCIAL_MAX_ITEMS", "5") or "5")))
CLOUDFLARE_CALLS_THIS_RUN = 0
MAX_CLOUDFLARE_CALLS_PER_RUN = 1
PLATFORM = os.getenv("SOCIAL_PLATFORM", "auto").strip().lower().lstrip(chr(92) + "/")

APIFY = os.getenv("APIFY_API_TOKEN", "").strip()
SCRAPECREATORS = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
SOCIALFETCH = os.getenv("SOCIALFETCH_API_KEY", "").strip()

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

PLATFORMS = ["linkedin", "facebook"]
# AUTO rotates only the two requested platforms during focused collection.
AUTO_PLATFORMS = ["facebook", "linkedin"]

ACTORS = {
    "linkedin": "harvestapi~linkedin-profile-scraper",
    "linkedin_company": "harvestapi~linkedin-company",
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

# Strict target: Chinese B2B industrial companies and relevant purchasing professionals only.
# No broad consumer, lifestyle, entertainment, unrelated tech, or generic lead collection.
INDUSTRY_TERMS = (
    "chemical", "chemicals", "petrochemical", "petrochemicals", "petroleum",
    "oil and gas", "oil & gas", "refinery", "steel", "metal", "polymer",
    "resin", "fertilizer", "solvent", "raw material", "raw materials",
    "feedstock", "industrial materials", "industrial chemical"
)
COMPANY_TERMS = (
    "company", "group", "limited", "ltd", "manufacturer", "manufacturing",
    "factory", "supplier", "production", "importer", "exporter", "refinery",
    "plant", "materials", "chemical industry", "industrial"
)
BUYER_ROLE_TERMS = (
    "procurement", "purchasing", "purchase manager", "buyer", "sourcing",
    "supply chain", "import manager", "procurement manager", "purchasing manager",
    "commercial manager", "trading", "raw material buyer"
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
PRIORITY_REGION_SIGNALS = ("jiangsu", "suzhou", "nanjing", "wuxi", "changzhou", "nantong")


def infer_social_industry(company, website, location, bio):
    text = " ".join([company, website, location, bio]).lower()
    if "petrochemical" in text or "polymer" in text or "feedstock" in text:
        return "petrochemicals"
    if "petroleum" in text or "oil and gas" in text or "oil & gas" in text or "refinery" in text:
        return "petroleum products"
    if "steel" in text or "metal" in text:
        return "steel"
    if "chemical" in text or "solvent" in text or "fertilizer" in text or "resin" in text:
        return "chemicals"
    return ""


def relevant_social_profile(platform, url, company, website, location, bio):
    text = " ".join([url, company, website, location, bio]).lower()
    if not company or company.lower() in {"unknown", "none", "n/a", "facebook", "linkedin"}:
        return False
    if not any(term in text for term in INDUSTRY_TERMS):
        return False
    china_match = any(signal in text for signal in CHINA_SIGNALS) or domain(url).endswith(".cn")
    if not china_match:
        return False
    # A corporate industrial supplier/manufacturer or an actual purchasing/trade role
    # is required; generic personal accounts and unrelated company pages are rejected.
    if not (
        any(term in text for term in COMPANY_TERMS)
        or any(term in text for term in BUYER_ROLE_TERMS)
    ):
        return False
    return True


NATIVE_QUERIES = {
    "linkedin": [
        "China Jiangsu chemical petrochemical procurement purchasing manager importer",
        "China industrial raw materials steel chemical supplier buyer",
    ],
    "facebook": [
        "Jiangsu China chemical manufacturer supplier",
        "China petrochemical industrial raw materials company",
    ],
}

DISCOVERY_METHODS = {
    "linkedin": "scrapecreators_linkedin_public_posts_search",
    "facebook": "scrapecreators_facebook_ad_library_company_search",
    "x": "apify_x_native_search",
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
    # Search the public Facebook Ad Library's company/page index. This is
    # platform-native public data, but it is not a general search of every Page.
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
        if not (path.startswith("/in/") or path.startswith("/company/")) or url in seen:
            continue
        seen.add(url)
        title = val(author.get("name") or post.get("authorName"))
        snippet = val(post.get("description") or post.get("text") or post.get("content"))
        LINKEDIN_SEARCH_RESULTS[url] = {
            "title": title,
            "snippet": snippet,
            "search_provider": "ScrapeCreators LinkedIn Search",
        }
        out.append({
            "url": url,
            "title": title,
            "content": " ".join([title, snippet]),
            "search_provider": "ScrapeCreators LinkedIn Search",
        })
    print(f"LinkedIn provider search: results={len(posts)}; candidate_profiles={len(out)}")
    return out

def native_candidates(platform, query):
    if platform == "facebook":
        return _facebook_native_candidates(query)
    if platform == "x":
        return _x_native_candidates(query)
    if platform == "linkedin":
        # Public search-index results only; no LinkedIn login, session cookies,
        # CAPTCHA bypass, or private/member-only data access.
        return _linkedin_provider_candidates(query)
    raise ValueError(f"Unsupported social platform: {platform}")


def social_candidate_score(platform, result, url):
    title = val(result.get("title"))
    snippet = val(result.get("content") or result.get("snippet") or result.get("description"))
    text = " ".join([title, snippet, url]).lower()
    industry_hits = sum(1 for term in INDUSTRY_TERMS if term in text)
    buyer_hits = sum(1 for term in BUYER_ROLE_TERMS if term in text)
    company_hits = sum(1 for term in COMPANY_TERMS if term in text)
    china_hits = sum(1 for term in CHINA_SIGNALS if term in text)
    # Discovery is intentionally broader than final acceptance. Require an
    # industrial signal, but let profile enrichment verify China and B2B fit;
    # search snippets often omit a company's headquarters or buyer role.
    if not industry_hits:
        return 0
    # Strongly prioritize China/Jiangsu evidence without requiring it in the
    # snippet. The final relevant_social_profile check remains strict.
    priority_hits = sum(1 for term in PRIORITY_REGION_SIGNALS if term in text)
    return industry_hits * 5 + buyer_hits * 4 + company_hits * 2 + china_hits * 3 + priority_hits * 4


def discover(p):
    """Return several ranked candidates across both targeted queries."""
    last_error = None
    ranked_by_url = {}
    for q in NATIVE_QUERIES[p]:
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
            score = social_candidate_score(p, item, u)
            if not score:
                continue
            current = ranked_by_url.get(u)
            candidate = (score, u, q, DISCOVERY_METHODS[p])
            if current is None or score > current[0]:
                ranked_by_url[u] = candidate

        # Stop querying this platform as soon as the first query has enough
        # candidates, reducing repeated provider calls and preserving API quota.
        if len(ranked_by_url) >= MAX_ITEMS:
            break

    ranked = sorted(ranked_by_url.values(), key=lambda item: item[0], reverse=True)
    # Gather a small candidate pool from each enabled platform. Final profile
    # validation remains strict; the main loop stops after five accepted leads.
    selected = ranked[:MAX_ITEMS]
    if selected:
        print(
            f"Native social discovery ranking: platform={p}; method={selected[0][3]}; "
            f"candidates={len(ranked)}; selected_for_enrichment={len(selected)}; "
            f"top_score={selected[0][0]}"
        )
    elif last_error:
        print(f"Social discovery ended without candidates: platform={p}; last_error={type(last_error).__name__}: {last_error}")
    return [(u, q, method) for _, u, q, method in selected]

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


def socialfetch_profile(p, url):
    if not SOCIALFETCH:
        raise RuntimeError("SOCIALFETCH_API_KEY missing")

    headers = {"x-api-key": SOCIALFETCH}
    handle = handle_from_url(url)

    if p == "linkedin":
        return get_json(
            "https://api.socialfetch.dev/v2/linkedin/profiles",
            headers=headers,
            params={"handle": handle}
        )

    if p == "facebook":
        return get_json(
            "https://api.socialfetch.dev/v1/facebook/profiles",
            headers=headers,
            params={"url": url}
        )

    return get_json(
        f"https://api.socialfetch.dev/v1/twitter/profiles/{quote(handle, safe='')}",
        headers=headers
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
        detail = (getattr(r, "text", "") or "").replace("\\n", " ")[:250]
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
        raise RuntimeError(
            f"cloudflare_http_{r.status_code}:{r.text[:300]}"
        )
    return r.json()


def provider_chain(p, url):
    errors = []

    # A non-empty HTTP response is not necessarily a successful profile fetch.
    # Require a company/profile name plus at least one substantive profile field
    # before accepting a provider; otherwise continue to the next adapter.
    if p == "linkedin" and "/company/" in urlparse(url).path.lower():
        # The configured Cloudflare Worker explicitly rejects LinkedIn URLs,
        # and SocialFetch's configured endpoint is for person profiles. Avoid
        # spending Worker quota or issuing a known-mismatched profile request.
        providers = [
            ("ScrapeCreators", lambda: scrapecreators_profile(p, url)),
            ("Apify", lambda: apify_profile(p, url)),
        ]
    elif p == "linkedin":
        # This Worker endpoint does not support LinkedIn URLs.
        providers = [
            ("ScrapeCreators", lambda: scrapecreators_profile(p, url)),
            ("Apify", lambda: apify_profile(p, url)),
            ("SocialFetch", lambda: socialfetch_profile(p, url)),
        ]
    else:
        providers = []
        if CLOUDFLARE_CALLS_THIS_RUN < MAX_CLOUDFLARE_CALLS_PER_RUN:
            providers.append(("CloudflareDeepBackup", lambda: cloudflare_profile(p, url)))
        providers.extend([
            ("ScrapeCreators", lambda: scrapecreators_profile(p, url)),
            ("Apify", lambda: apify_profile(p, url)),
            ("SocialFetch", lambda: socialfetch_profile(p, url)),
        ])

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

    if provider == "SocialFetch":
        xdata = x.get("data") if isinstance(x.get("data"), dict) else x
        return (
            val(xdata.get("displayName") or xdata.get("name") or xdata.get("handle")),
            val(xdata.get("website") or xdata.get("externalUrl")),
            val(xdata.get("location")),
            val(xdata.get("displayName") or xdata.get("name") or xdata.get("handle")),
            val(xdata.get("email")), val(xdata.get("phone")),
            val(xdata.get("headline") or xdata.get("about") or xdata.get("bio") or xdata.get("description")),
            val(xdata.get("followers")),
            val(xdata.get("verified")), fallback
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


def main():
    # Auto mode actively searches both allowed networks on every run.
    # Manual mode still restricts collection to the explicitly selected network.
    is_auto = PLATFORM not in PLATFORMS
    platforms_to_collect = AUTO_PLATFORMS if is_auto else [PLATFORM]
    found = []
    for target_platform in platforms_to_collect:
        platform_candidates = discover(target_platform)
        found.extend((target_platform, url, query, method)
                     for url, query, method in platform_candidates)
        print(
            f"SOCIAL_DISCOVERY_SUMMARY platform={target_platform}; "
            f"candidates={len(platform_candidates)}"
        )

    if not found:
        print("No Facebook or LinkedIn candidates found")
        return

    rows = []
    fields = []

    if LEADS.exists():
        with LEADS.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
            fields = list(rows[0].keys()) if rows else []

    for f in FIELDS:
        if f not in fields:
            fields.append(f)

    seen = {
        (r.get("platform", ""), r.get("social_url", "").rstrip("/"))
        for r in rows if r.get("social_url")
    }

    added = 0
    provider_counts = {
        "ScrapeCreators": 0,
        "Apify": 0,
        "SocialFetch": 0,
        "CloudflareDeepBackup": 0,
        "PublicSearchIndex": 0,
        "failed": 0
    }

    for p, url, query, discovery_method in found:
        if added >= MAX_ITEMS:
            break
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
                "note": "Public social data only; profile enrichment uses Cloudflare and configured social providers."
            }, ensure_ascii=False)
        )

        provider, data, errors = provider_chain(p, url)
        if not provider:
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
            print(f"Rejected low-confidence social result: platform={p}; discovered_url={url}; returned_profile_url={returned_url or 'NOT_RETURNED'}")
            continue

        if (p, surl.rstrip("/")) in seen:
            continue

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
            "country": "China",
            "industry": inferred_industry,
            "buyer_type": "industrial buyer / manufacturer / importer",
            "product_interest": product_labels.get(inferred_industry, inferred_industry),
            "contact_person": contact,
            "email": email,
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
        f"Social collection: platforms={','.join(platforms_to_collect)}; "
        f"discovered_candidates={len(found)}; added={added}; target_cap={MAX_ITEMS}; "
        f"cloudflare_calls={CLOUDFLARE_CALLS_THIS_RUN}/{MAX_CLOUDFLARE_CALLS_PER_RUN}; "
        f"providers={json.dumps(provider_counts, ensure_ascii=False)}"
    )


if __name__ == "__main__":
    main()
