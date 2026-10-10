import csv, json, os, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEADS = DATA / "leads.csv"
DATA.mkdir(exist_ok=True)

MAX_ITEMS = 1  # Respect the Worker daily collection cap: one profile per run.
PLATFORM = os.getenv("SOCIAL_PLATFORM", "auto").strip().lower().lstrip(chr(92) + "/")

APIFY = os.getenv("APIFY_API_TOKEN", "").strip()
SCRAPECREATORS = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
SOCIALFETCH = os.getenv("SOCIALFETCH_API_KEY", "").strip()

CLOUDFLARE_URL = os.getenv(
    "SOCIAL_DEEP_BACKUP_URL",
    "https://patient-moon-b87e.mohamadsbrfit760li.workers.dev"
).strip().rstrip("/")
CLOUDFLARE_TOKEN = os.getenv("SOCIAL_DEEP_BACKUP_TOKEN", "").strip()
SEARXNG_URL = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
SEARXNG_FALLBACKS = [
    "https://searxng.website",
    "https://searxng.eshnetwork.space",
    "https://search.mectov.my.id",
]

RUN_ID = os.getenv(
    "GITHUB_RUN_ID",
    datetime.now(timezone.utc).strftime("social-%Y%m%d%H%M%S")
)
NOW = datetime.now(timezone.utc).isoformat()

PLATFORMS = ["linkedin", "instagram", "facebook", "x"]

ACTORS = {
    "linkedin": "harvestapi~linkedin-profile-scraper",
    "instagram": "apify~instagram-profile-scraper",
    "facebook": "apify~facebook-pages-scraper",
    "x": "apidojo~tweet-scraper",
}

QUERIES = {
    "linkedin": [
        'site:linkedin.com/in/ ("procurement manager" OR "purchasing manager" OR "sourcing manager") (petrochemical OR chemical OR steel OR petroleum) China',
        'site:linkedin.com/in/ ("import manager" OR "procurement director" OR "purchase manager") (chemical OR steel OR energy) China'
    ],
    "instagram": [
        'site:instagram.com/ ("petrochemical" OR "chemical" OR "steel") China company',
        'site:instagram.com/ ("industrial" OR "manufacturer" OR "importer") China chemical steel'
    ],
    "facebook": [
        'site:facebook.com/ "petrochemical" China company',
        'site:facebook.com/ "chemical manufacturer" China'
    ],
    "x": [
        'site:x.com/ ("petrochemical" OR "chemical") China company',
        'site:x.com/ ("steel" OR "industrial") China manufacturer'
    ]
}

FIELDS = [
    "company_name", "website", "country", "industry", "buyer_type",
    "product_interest", "contact_person", "email", "whatsapp", "phone",
    "linkedin", "source", "evidence", "lead_score", "run_id", "collected_at",
    "search_query", "social_url", "platform", "username", "followers",
    "verified", "social_bio", "source_provider"
]

session = requests.Session()
session.headers.update({"User-Agent": "rozhanglobal-social-collector/1.0"})


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


def good(p, u):
    d = domain(u)
    ok = {
        "linkedin": {"linkedin.com"},
        "instagram": {"instagram.com"},
        "facebook": {"facebook.com"},
        "x": {"x.com", "twitter.com"}
    }[p]
    return d in ok and not any(
        x in u.lower()
        for x in ["/search", "/explore", "/hashtag", "/jobs"]
    )

INDUSTRY_TERMS = (
    "chemical", "petrochemical", "petroleum", "oil and gas", "oil & gas",
    "refinery", "steel", "metal", "industrial", "manufacturer", "manufacturing",
    "factory", "polymer", "resin", "fertilizer", "solvent", "solar", "wind",
    "battery", "renewable energy", "raw material", "feedstock"
)
BUYER_TERMS = (
    "company", "group", "limited", "ltd", "manufacturer", "manufacturing",
    "factory", "industrial", "supplier", "production", "procurement",
    "importer", "refinery", "plant", "products", "materials"
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

def relevant_social_profile(platform, url, company, website, location, bio):
    text = " ".join([url, company, website, location, bio]).lower()
    host = domain(url)
    if not company or company.lower() in {"unknown", "none", "n/a", "facebook", "instagram", "linkedin"}:
        return False
    if not any(term in text for term in INDUSTRY_TERMS):
        return False
    if not any(term in text for term in BUYER_TERMS):
        return False
    if not (host.endswith(".cn") or any(signal in text for signal in CHINA_SIGNALS)):
        return False
    return True


def _searx_results(data, num=10):
    if not isinstance(data, dict):
        return []
    return data.get("results") or []


def _searx_html_results(html, num=10):
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
            out.append({"url": u})
            if len(out) >= num:
                break
    return out


def search(q):
    bases = []
    if SEARXNG_URL:
        bases.append(SEARXNG_URL)
    bases.extend(x for x in SEARXNG_FALLBACKS if x not in bases)

    last_error = None
    for base in bases:
        try:
            r = session.get(
                base + "/search",
                params={"q": q, "format": "json", "categories": "general", "language": "en", "pageno": 1},
                timeout=30,
            )
            if r.ok:
                results = _searx_results(r.json(), 10)
                if results:
                    print(f"SearXNG discovery: {base} | results={len(results)}")
                    return results
        except Exception as exc:
            last_error = exc

        try:
            r = session.get(
                base + "/search",
                params={"q": q, "categories": "general", "language": "en", "pageno": 1},
                timeout=30,
            )
            r.raise_for_status()
            results = _searx_html_results(r.text, 10)
            if results:
                print(f"SearXNG HTML discovery: {base} | results={len(results)}")
                return results
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"SearXNG discovery unavailable across {len(bases)} instances: {last_error}")


def discover(p):
    out = []
    seen = set()
    for q in QUERIES[p]:
        for x in search(q):
            u = (x.get("url") or "").rstrip("/")
            if good(p, u) and u not in seen:
                seen.add(u)
                out.append((u, q))
                if len(out) >= MAX_ITEMS:
                    return out
    return out


def get_json(url, headers=None, params=None, timeout=45):
    r = session.get(
        url,
        headers=headers or {},
        params=params or {},
        timeout=timeout
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError("provider_returned_success_false")
    return data


def scrapecreators_profile(p, url):
    if not SCRAPECREATORS:
        raise RuntimeError("SCRAPECREATORS_API_KEY missing")

    headers = {"x-api-key": SCRAPECREATORS}

    if p == "linkedin":
        return get_json(
            "https://api.scrapecreators.com/v1/linkedin/profile",
            headers=headers,
            params={"url": url}
        )

    if p == "instagram":
        return get_json(
            "https://api.scrapecreators.com/v1/instagram/profile",
            headers=headers,
            params={
                "handle": handle_from_url(url),
                "trim": "true",
                "cache_max_age": "7d"
            }
        )

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

    if p == "instagram":
        return get_json(
            f"https://api.socialfetch.dev/v1/instagram/profiles/{quote(handle, safe='')}",
            headers=headers
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
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("items", [])


def apify_profile(p, url):
    if p == "linkedin":
        return actor(p, {
            "profileScraperMode": "Profile details no email",
            "queries": [url]
        })
    if p == "instagram":
        return actor(p, {"usernames": [handle_from_url(url)]})
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

    # Primary: our Cloudflare Worker. Only fall through when it fails/returns empty.
    # This keeps paid/provider quotas largely untouched during healthy runs.
    providers = [
        ("CloudflareDeepBackup", lambda: cloudflare_profile(p, url)),
        ("ScrapeCreators", lambda: scrapecreators_profile(p, url)),
        ("Apify", lambda: apify_profile(p, url)),
        ("SocialFetch", lambda: socialfetch_profile(p, url)),
    ]

    for name, fn in providers:
        try:
            data = fn()
            if data:
                print(f"Social provider success: {name}")
                return name, data, errors
            errors.append(f"{name}:empty")
        except Exception as e:
            errors.append(f"{name}:{e}")
            print(f"Social provider failed: {name} | {e}")
            # If the Worker explicitly reports its daily cap, do not consume
            # paid fallback-provider quota to bypass that configured safeguard.
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


def normalize_provider(p, provider, data, fallback):
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
            return (
                val(x.get("name")), "", val(x.get("location")),
                val(x.get("name")), "", "", val(x.get("about")),
                val(x.get("followers")), val(x.get("verified")), fallback
            )
        if p == "instagram":
            u = x.get("data", {}).get("user", {}) if isinstance(x.get("data"), dict) else {}
            return (
                val(u.get("full_name") or u.get("username")),
                val(u.get("external_url")), "",
                val(u.get("full_name") or u.get("username")),
                "", "", val(u.get("biography")),
                val((u.get("edge_followed_by") or {}).get("count")),
                val(u.get("is_verified")), fallback
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

    if p == "instagram":
        bio = val(x.get("biography") or x.get("bio"))
        return (
            val(x.get("fullName") or x.get("name") or x.get("username")),
            val(x.get("externalUrl") or x.get("website")), val(x.get("location")),
            val(x.get("fullName") or x.get("username")), "", "", bio,
            val(x.get("followersCount") or x.get("followers")),
            val(x.get("verified")), val(x.get("url")) or fallback
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


def main():
    # AUTO rotates one network per UTC day so repeated manual triggers cannot
    # accidentally pin the automation to a single weekday-based network.
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    p = PLATFORM if PLATFORM in PLATFORMS else PLATFORMS[
        (day_of_year - 1) % len(PLATFORMS)
    ]
    found = discover(p)

    if not found:
        print(f"No {p} candidates")
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
        (r.get("platform", ""), r.get("social_url", ""))
        for r in rows if r.get("social_url")
    }

    added = 0
    provider_counts = {
        "ScrapeCreators": 0,
        "Apify": 0,
        "SocialFetch": 0,
        "CloudflareDeepBackup": 0,
        "failed": 0
    }

    for url, query in found[:MAX_ITEMS]:
        provider, data, errors = provider_chain(p, url)

        if not provider:
            provider_counts["failed"] += 1
            print(f"All providers failed for {url}: " + " | ".join(errors))
            continue

        provider_counts[provider] += 1
        company, web, loc, contact, email, phone, bio, followers, verified, surl = (
            normalize_provider(p, provider, data, url)
        )

        if not relevant_social_profile(p, surl or url, company, web, loc, bio):
            print(f"Rejected low-confidence social result: platform={p}; url={url}")
            continue

        if (p, surl) in seen:
            continue

        row = {f: "" for f in fields}
        row.update({
            "company_name": company,
            "website": web,
            "country": "China",
            "industry": p,
            "buyer_type": "social_discovery",
            "product_interest": "petroleum products / chemicals / petrochemicals / steel / renewable energy",
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
            "social_url": surl,
            "platform": p,
            "username": handle_from_url(surl),
            "followers": followers,
            "verified": verified,
            "social_bio": bio[:3000],
            "source_provider": provider
        })

        rows.append(row)
        seen.add((p, surl))
        added += 1

    with LEADS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(
        f"Social collection: {p}; discovered={len(found)}; added={added}; "
        f"cap={MAX_ITEMS}; providers={json.dumps(provider_counts, ensure_ascii=False)}"
    )


if __name__ == "__main__":
    main()
