import csv, json, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEADS = DATA / "leads.csv"
DATA.mkdir(exist_ok=True)

MAX_ITEMS = min(int(os.getenv("SOCIAL_MAX_ITEMS", "3")), 3)
PLATFORM = os.getenv("SOCIAL_PLATFORM", "auto").lower().strip()

APIFY = os.getenv("APIFY_API_TOKEN", "").strip()
SCRAPECREATORS = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
SOCIALFETCH = os.getenv("SOCIALFETCH_API_KEY", "").strip()
YDC = os.getenv("YDC_API_KEY", "").strip()

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


def search(q):
    if not YDC:
        raise RuntimeError("YDC_API_KEY missing")
    r = session.get(
        "https://api.you.com/v1/search",
        json={"query": q, "count": 10},
        headers={"X-API-Key": YDC, "Accept": "application/json"},
        timeout=30
    )
    r.raise_for_status()
    return (r.json().get("results") or {}).get("web") or []


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

    for name, fn in [
        ("ScrapeCreators", lambda: scrapecreators_profile(p, url)),
        ("Apify", lambda: apify_profile(p, url)),
        ("SocialFetch", lambda: socialfetch_profile(p, url)),
        ("CloudflareDeepBackup", lambda: cloudflare_profile(p, url)),
    ]:
        try:
            data = fn()
            if data:
                return name, data, errors
            errors.append(f"{name}:empty")
        except Exception as e:
            errors.append(f"{name}:{e}")

    return "", None, errors


def normalize_provider(p, provider, data, fallback):
    if provider == "CloudflareDeepBackup":
        result = data.get("result") if isinstance(data, dict) else data
        return "", "", "China", "", "", "", val(result)[:3000], "", "", fallback

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
    p = PLATFORM if PLATFORM in PLATFORMS else PLATFORMS[
        datetime.now(timezone.utc).weekday() % 4
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
