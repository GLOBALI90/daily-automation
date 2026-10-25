import csv, json, os, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, quote
import requests
from google_sheet_store import GoogleSheetStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEADS = DATA / "leads.csv"
PERSONAL_PROFILES = DATA / "personal_profiles.csv"
PERSONAL_PROFILE_FIELDS = ["platform", "public_name", "profile_url", "username", "run_id", "collected_at"]
DATA.mkdir(exist_ok=True)

# Product-first lead discovery. Country/product are chosen manually in workflow_dispatch.
PRODUCT_NAME = os.getenv("PRODUCT_NAME", "industrial lubricants").strip() or "industrial lubricants"
TARGET_MARKET = os.getenv("TARGET_MARKET", "China").strip() or "China"


def infer_buyer_types(product):
    """Infer buyer roles from the actual selected product, not one universal template."""
    value = re.sub(r"\s+", " ", str(product or "").strip().lower())
    profiles = [
        (("petroleum products — all", "petroleum products - all", "crude oil", "fuel oil", "gasoline", "petrol", "diesel", "gasoil", "jet fuel", "aviation fuel", "kerosene", "lpg", "lng", "bitumen", "asphalt", "base oil", "lubricant", "grease", "hydraulic oil", "gear oil", "compressor oil", "turbine oil", "paraffin wax", "petroleum coke", "sulfur"),
         "refineries, asphalt and road contractors, fuel distributors, lubricant blenders, industrial plants, fleet operators, importers, distributors and procurement teams"),
        (("chemicals — all", "chemicals - all", "industrial chemical", "specialty chemical", "caustic soda", "soda ash", "methanol", "ethanol", "ammonia", "urea", "chlorine", "acid", "hydrogen peroxide", "solvent", "acetone", "toluene", "xylene", "glycol", "glycerin", "titanium dioxide", "carbon black", "pigment", "resin", "adhesive", "detergent", "surfactant", "water-treatment", "agrochemical"),
         "chemical manufacturers and formulators, industrial plants, water-treatment operators, agriculture-input distributors, importers, distributors and procurement teams"),
        (("petrochemicals — all", "petrochemicals - all", "petrochemical", "ethylene", "propylene", "polyethylene", "polypropylene", "pvc", "pet resin", "polystyrene", "styrene", "butadiene", "aromatics", "monoethylene glycol", "meg", "pta", "paraxylene", "phenol", "acetic acid", "synthetic rubber"),
         "polymer and plastics processors, packaging manufacturers, converters, synthetic-rubber producers, chemical distributors, importers and procurement teams"),
        (("steel — all", "steel - all", "steel", "rebar", "wire rod", "billet", "slab", "stainless", "hot-rolled", "cold-rolled", "galvanized", "color-coated", "steel pipe", "steel scrap", "structural steel"),
         "steel service centers, fabricators, construction and infrastructure contractors, pipe makers, machinery manufacturers, metal processors, steel traders and procurement teams"),
        (("renewable energy — all", "renewable energy - all", "renewable", "solar", "photovoltaic", "pv module", "solar cell", "inverter", "wind turbine", "battery", "bess", "green hydrogen", "electrolyzer", "electrolyser"),
         "renewable-energy project developers, EPC contractors, utilities, solar and wind installers, battery integrators, equipment distributors and procurement teams"),
    ]
    for terms, roles in profiles:
        if any(term in value for term in terms):
            return roles
    return "direct B2B buyers, industrial consumers, manufacturers using the selected product, importers, distributors, traders, procurement teams and product-specific end users"

# The system infers customer types; no manual buyer-type field is required.
BUYER_TYPES = infer_buyer_types(PRODUCT_NAME)
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
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "").strip()
COLLECTION_MODE = os.getenv("COLLECTION_MODE", "social").strip().lower()
PRODUCT_LEADS = DATA / "product_leads.csv"
PRODUCT_LEAD_FIELDS = [
    "company_name", "website", "country", "buyer_type", "product_interest",
    "email", "phone", "whatsapp", "source_url", "evidence", "verification_status",
    "ai_customer_fit", "ai_confidence", "ai_recommended_buyer_type",
    "ai_reason", "ai_review_status", "run_id", "collected_at"
]
FIRECRAWL_SEARCHES_THIS_RUN = 0
MAX_FIRECRAWL_SEARCHES_PER_RUN = 1
FIRECRAWL_SCRAPES_THIS_RUN = 0
MAX_FIRECRAWL_SCRAPES_PER_RUN = 1
GITHUB_RUN_NUMBER = int(os.getenv("GITHUB_RUN_NUMBER", "0") or "0")
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


def firecrawl_country_code(country):
    """Map common target-market names to Firecrawl's ISO-2 country filter."""
    value = re.sub(r"\s+", " ", val(country)).strip().lower()
    codes = {
        "china": "cn", "turkey": "tr", "türkiye": "tr",
        "united states": "us", "united states of america": "us", "usa": "us", "us": "us",
        "united kingdom": "uk", "great britain": "uk", "uk": "uk",
        "india": "in", "germany": "de", "france": "fr", "italy": "it",
        "spain": "es", "canada": "ca", "australia": "au", "japan": "jp",
        "south korea": "kr", "korea": "kr", "indonesia": "id", "vietnam": "vn",
        "thailand": "th", "pakistan": "pk", "bangladesh": "bd", "malaysia": "my",
        "singapore": "sg", "netherlands": "nl", "belgium": "be", "poland": "pl",
        "mexico": "mx", "egypt": "eg", "iran": "ir", "russia": "ru",
        "south africa": "za", "nigeria": "ng", "brazil": "br", "argentina": "ar",
        "chile": "cl", "colombia": "co", "taiwan": "tw", "hong kong": "hk",
        "kazakhstan": "kz", "uzbekistan": "uz", "qatar": "qa", "oman": "om",
        "iraq": "iq", "afghanistan": "af", "switzerland": "ch", "austria": "at",
        "sweden": "se", "norway": "no", "denmark": "dk", "finland": "fi",
        "portugal": "pt", "greece": "gr", "czechia": "cz", "czech republic": "cz",
        "romania": "ro", "hungary": "hu", "ukraine": "ua", "uae": "ae",
        "united arab emirates": "ae", "saudi arabia": "sa", "new zealand": "nz",
        "philippines": "ph", "sri lanka": "lk", "nepal": "np", "israel": "il",
        "morocco": "ma", "kenya": "ke", "ethiopia": "et", "peru": "pe",
        "venezuela": "ve", "ecuador": "ec", "serbia": "rs", "croatia": "hr",
        "slovakia": "sk", "slovenia": "si", "bulgaria": "bg", "lithuania": "lt",
        "latvia": "lv", "estonia": "ee", "ireland": "ie", "iceland": "is",
        "luxembourg": "lu", "cyprus": "cy", "malta": "mt", "georgia": "ge",
        "armenia": "am", "azerbaijan": "az",
    }
    if re.fullmatch(r"[a-z]{2}", value):
        return value
    aliases = {
        "iran, islamic republic of": "ir", "russian federation": "ru",
        "viet nam": "vn", "korea, republic of": "kr",
        "korea, democratic people's republic of": "kp",
        "bolivia, plurinational state of": "bo",
        "venezuela, bolivarian republic of": "ve",
        "moldova, republic of": "md", "tanzania, united republic of": "tz",
        "taiwan, province of china": "tw", "palestine, state of": "ps",
        "micronesia, federated states of": "fm",
        "congo, the democratic republic of the": "cd",
        "brunei darussalam": "bn", "cabo verde": "cv",
        "côte d'ivoire": "ci", "czechia": "cz", "Türkiye".lower(): "tr",
        "united states": "us", "united kingdom": "uk",
        "united arab emirates": "ae", "hong kong": "hk", "macao": "mo",
        "curaçao": "cw", "réunion": "re", "eswatini": "sz",
        "north macedonia": "mk", "timor-leste": "tl",
        "saint martin (french part)": "mf", "sint maarten (dutch part)": "sx",
    }
    return codes.get(value, aliases.get(value, ""))


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
# Product-aware synonyms: selected labels are translated into terms buyers use on social pages.
PRODUCT_SYNONYM_GROUPS = {
    "petroleum products": ("crude oil", "fuel oil", "gasoline", "petrol", "diesel", "gasoil", "jet fuel", "aviation fuel", "kerosene", "lpg", "lng", "bitumen", "asphalt", "base oil", "lubricant", "grease", "hydraulic oil", "gear oil", "compressor oil", "turbine oil", "paraffin wax", "petroleum coke", "sulfur"),
    "chemicals": ("industrial chemicals", "specialty chemicals", "caustic soda", "soda ash", "methanol", "ethanol", "ammonia", "urea", "chlorine", "hydrochloric acid", "sulfuric acid", "hydrogen peroxide", "industrial solvents", "acetone", "toluene", "xylene", "ethylene glycol", "glycerin", "titanium dioxide", "carbon black", "pigments", "resins", "adhesives", "detergents", "surfactants", "water-treatment chemicals", "agrochemicals"),
    "petrochemicals": ("ethylene", "propylene", "polyethylene", "polypropylene", "pvc resin", "pet resin", "polystyrene", "styrene monomer", "butadiene", "aromatics", "monoethylene glycol", "pta", "paraxylene", "phenol", "acetic acid", "synthetic rubber"),
    "steel": ("hot-rolled coil", "cold-rolled coil", "galvanized steel", "pre-painted steel", "color-coated steel", "rebar", "wire rod", "billets", "slabs", "steel pipes", "stainless steel", "steel scrap", "structural steel", "flat steel"),
    "renewable energy": ("solar panels", "pv modules", "solar cells", "solar inverters", "solar mounting structures", "wind turbines", "wind-turbine components", "battery energy storage systems", "bess", "lithium-ion batteries", "green hydrogen", "electrolyzers"),
}
PRODUCT_ALIASES = {
    "bitumen / asphalt": ("bitumen", "asphalt", "road bitumen", "paving bitumen", "asphalt binder", "bitumen emulsion", "asphalt mix", "沥青"),
    "base oils": ("base oil", "base oils", "lubricant base stock", "group i base oil", "group ii base oil", "group iii base oil"),
    "industrial lubricants": ("industrial lubricant", "industrial lubricants", "lubricating oil", "lubricating oils", "lubricant blending", "润滑油", "工业润滑油"),
    "industrial greases": ("industrial grease", "lubricating grease", "greases", "润滑脂"),
    "automotive lubricants": ("automotive lubricant", "automotive lubricants", "engine oil", "motor oil", "transmission fluid", "车用润滑油"),
    "hydraulic oils": ("hydraulic oil", "hydraulic oils", "hydraulic fluid"),
    "gear oils": ("gear oil", "gear oils", "industrial gear lubricant"),
    "compressor oils": ("compressor oil", "compressor oils"),
    "turbine oils": ("turbine oil", "turbine oils"),
    "metalworking fluids": ("metalworking fluid", "metalworking fluids", "cutting fluid", "machining fluid", "coolant"),
    "paraffin wax": ("paraffin wax", "fully refined paraffin wax", "semi refined paraffin wax"),
    "petroleum coke": ("petroleum coke", "petcoke", "pet coke"),
    "sulfur": ("sulfur", "sulphur", "granular sulfur", "sulfur prills"),
    "caustic soda": ("caustic soda", "sodium hydroxide", "naoh"),
    "soda ash": ("soda ash", "sodium carbonate"),
    "methanol": ("methanol", "methyl alcohol"),
    "urea": ("urea", "fertilizer urea", "prilled urea", "granular urea"),
    "hydrochloric acid": ("hydrochloric acid", "hcl", "muriatic acid"),
    "sulfuric acid": ("sulfuric acid", "sulphuric acid", "h2so4"),
    "hydrogen peroxide": ("hydrogen peroxide", "h2o2"),
    "ethylene glycol": ("ethylene glycol", "meg", "monoethylene glycol"),
    "titanium dioxide": ("titanium dioxide", "tio2"),
    "carbon black": ("carbon black",),
    "polyethylene (pe)": ("polyethylene", "pe resin", "hdpe", "ldpe", "lldpe"),
    "polypropylene (pp)": ("polypropylene", "pp resin"),
    "pvc resin": ("pvc resin", "polyvinyl chloride"),
    "pet resin": ("pet resin", "polyethylene terephthalate"),
    "hot-rolled coil": ("hot-rolled coil", "hot rolled steel", "hrc"),
    "cold-rolled coil": ("cold-rolled coil", "cold rolled steel", "crc"),
    "galvanized steel": ("galvanized steel", "galvanised steel", "gi coil"),
    "rebar": ("rebar", "reinforcing steel", "reinforcing bar"),
    "wire rod": ("wire rod", "steel wire rod"),
    "steel pipes and tubes": ("steel pipe", "steel pipes", "steel tube", "steel tubes"),
    "solar panels / pv modules": ("solar panel", "solar panels", "pv module", "photovoltaic module"),
    "solar inverters": ("solar inverter", "pv inverter"),
    "wind turbines": ("wind turbine", "wind turbines"),
    "battery energy storage systems (bess)": ("battery energy storage", "bess", "energy storage system"),
    "lithium-ion batteries": ("lithium-ion battery", "lithium ion battery", "li-ion battery"),
}
_product_key = re.sub(r"\s+", " ", PRODUCT_NAME.lower()).strip()
_product_family = next(
    (key for key in PRODUCT_SYNONYM_GROUPS
     if _product_key in {key, f"{key} — all", f"{key} - all"}),
    ""
)
PRODUCT_TERMS = PRODUCT_SYNONYM_GROUPS.get(
    _product_family, PRODUCT_ALIASES.get(_product_key, ())
)
if not PRODUCT_TERMS:
    _product_tokens = [token for token in re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", PRODUCT_NAME.lower()) if len(token) > 2]
    PRODUCT_TERMS = tuple(dict.fromkeys([PRODUCT_NAME.lower(), *_product_tokens]))
else:
    PRODUCT_TERMS = tuple(dict.fromkeys((PRODUCT_NAME.lower(), *PRODUCT_TERMS)))

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
    "linkedin": "native_linkedin_social_search",
    "facebook": "native_facebook_social_search",
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
    """Search only Facebook's own public page/ad-library index; never use Google."""
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
        if not url or not good("facebook", url):
            continue
        out.append({
            "url": url,
            "title": val(item.get("page_name") or item.get("name") or alias),
            "content": val(item.get("description") or item.get("about") or
                           item.get("category") or item.get("category_name")),
            "search_provider": "ScrapeCreators Facebook Page Search",
        })
    print(f"Facebook native page search: candidates={len(out)}; web_search_used=false")
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
    """Use only provider endpoints dedicated to the requested social network."""
    if platform == "facebook":
        return _facebook_native_candidates(query)
    if platform == "linkedin":
        out = []
        seen = set()
        # First use LinkedIn-specific public post search, then LinkedIn company search.
        if SCRAPECREATORS:
            try:
                out.extend(_linkedin_provider_candidates(query))
            except Exception as exc:
                print(f"ScrapeCreators LinkedIn native search failed: {type(exc).__name__}: {exc}")
        if APIFY:
            try:
                for item in _apify_linkedin_company_candidates(query):
                    url = val(item.get("url")).rstrip("/")
                    if url and url not in seen:
                        seen.add(url)
                        out.append(item)
            except Exception as exc:
                print(f"Apify LinkedIn native search failed: {type(exc).__name__}: {exc}")
        if not out:
            print("LinkedIn native search returned no candidates; no generic web fallback will be used.")
        return out
    raise ValueError(f"Unsupported social platform: {platform}")


def personal_profile_candidate_score(result, url):
    """Score public LinkedIn people using role evidence; query context supplies product/market."""
    if not urlparse(url).path.lower().startswith("/in/"):
        return 0
    text = " ".join([
        val(result.get("title")), val(result.get("content")),
        val(result.get("headline")), val(result.get("current_company")),
        val(result.get("location")), url
    ]).lower()
    role_text = " ".join([
        val(result.get("title")), val(result.get("headline")),
        val(result.get("current_company"))
    ]).lower()
    product_hits = sum(1 for term in PRODUCT_TERMS if term in text)
    market_hits = sum(1 for term in MARKET_SIGNALS if term in text)
    role_hits = sum(1 for term in BUYER_ROLE_TERMS + DECISION_MAKER_TERMS if term in role_text)
    # Do not demand all three signals in a short snippet. Search itself is scoped
    # to product + target market; a credible business role is enough to rank a person.
    if not role_hits and not product_hits:
        return 0
    return 6 + min(product_hits, 4) * 2 + min(role_hits, 4) + min(market_hits, 2)

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
    product_hits = sum(1 for term in PRODUCT_TERMS if term in text)
    buyer_hits = sum(1 for term in BUYER_TYPE_TERMS if term in text)
    company_hits = sum(1 for term in COMPANY_TERMS if term in text)
    market_hits = sum(1 for term in MARKET_SIGNALS if term in text)
    # The search query already contains product + target market. Short snippets
    # often omit location or buyer words, so those are ranking signals, not gates.
    if not product_hits and not (buyer_hits and company_hits):
        return 0
    return product_hits * 5 + buyer_hits * 2 + company_hits + market_hits * 2

def pace_cloudflare_request():
    global LAST_CLOUDFLARE_REQUEST_AT
    if LAST_CLOUDFLARE_REQUEST_AT:
        wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - LAST_CLOUDFLARE_REQUEST_AT)
        if wait > 0:
            print(f"CLOUDFLARE_REQUEST_PACING sleep_seconds={wait:.1f}")
            time.sleep(wait)
    LAST_CLOUDFLARE_REQUEST_AT = time.monotonic()


def cloudflare_public_web_candidates(query, platform="web"):
    """Use the bounded Cloudflare Browser Worker for public web or Google Maps discovery."""
    if platform not in ("web", "maps"):
        raise ValueError("Cloudflare public search platform must be web or maps")
    if not CLOUDFLARE_TOKEN:
        raise RuntimeError("SOCIAL_DEEP_BACKUP_TOKEN missing for Cloudflare public search")
    global CLOUDFLARE_CALLS_THIS_RUN
    if CLOUDFLARE_CALLS_THIS_RUN >= MAX_CLOUDFLARE_CALLS_PER_RUN:
        raise RuntimeError("cloudflare_per_run_cap_reached")
    CLOUDFLARE_CALLS_THIS_RUN += 1
    pace_cloudflare_request()
    started = time.monotonic()
    response = session.post(
        f"{CLOUDFLARE_URL}/search",
        headers={"Authorization": f"Bearer {CLOUDFLARE_TOKEN}", "Content-Type": "application/json"},
        json={"query": query, "platform": platform},
        timeout=90,
    )
    audit = {"route": "/search", "platform": platform, "http_status": response.status_code,
             "elapsed_seconds": round(time.monotonic() - started, 2)}
    if response.status_code >= 400:
        audit["outcome"] = "http_error"
        CLOUDFLARE_AUDIT.append(audit)
        if response.status_code == 429:
            raise RuntimeError("cloudflare_http_429_daily_limit_reached")
        raise RuntimeError(f"cloudflare_public_search_http_{response.status_code}:{response.text[:250]}")
    payload = response.json()
    details = payload.get("candidate_details", []) if isinstance(payload, dict) else []
    candidates, seen = [], set()
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        url = val(item.get("url")).split("#", 1)[0].rstrip("/")
        if not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        snippet = val(item.get("snippet"))
        candidates.append({
            "url": url, "title": val(item.get("title")),
            "description": snippet, "content": snippet,
            "search_provider": "Cloudflare Google Maps Search" if platform == "maps" else "Cloudflare Public Web Search",
            "is_google_maps": bool(item.get("is_google_maps")) or platform == "maps",
        })
        if len(candidates) >= MAX_ITEMS:
            break
    audit.update({"outcome": "success", "candidate_count": len(candidates)})
    CLOUDFLARE_AUDIT.append(audit)
    print(f"CLOUDFLARE_PUBLIC_SEARCH platform={platform}; http_status={response.status_code}; "
          f"candidates={len(candidates)}; worker_actions={CLOUDFLARE_CALLS_THIS_RUN}/{MAX_CLOUDFLARE_CALLS_PER_RUN}")
    return candidates


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
    """Discover candidates exclusively through LinkedIn/Facebook-specific providers."""
    global DUPLICATE_PREFILTER_SKIPPED
    ranked_by_url = {}
    personal_by_url = {}
    last_error = None

    for query in NATIVE_QUERIES[p]:
        if len(ranked_by_url) >= MAX_ITEMS and (
            p != "linkedin" or len(personal_by_url) >= 3
        ):
            break
        try:
            results = native_candidates(p, query)
        except Exception as exc:
            last_error = exc
            print(
                f"Native social discovery failed: platform={p}; "
                f"method={DISCOVERY_METHODS[p]}; error={type(exc).__name__}: {exc}"
            )
            continue

        for item in results:
            url = val(item.get("url")).rstrip("/")
            if not url or not good(p, url):
                continue
            if is_existing_candidate(p, url):
                DUPLICATE_PREFILTER_SKIPPED += 1
                print(f"Skipping previously stored social candidate: platform={p}; url={url}")
                continue
            score = social_candidate_score(p, item, url)
            if not score:
                continue
            is_personal = p == "linkedin" and urlparse(url).path.lower().startswith("/in/")
            target = personal_by_url if is_personal else ranked_by_url
            current = target.get(url)
            candidate = (score, url, query, DISCOVERY_METHODS[p])
            if current is None or score > current[0]:
                target[url] = candidate

    ranked = sorted(ranked_by_url.values(), key=lambda item: item[0], reverse=True)
    personal_ranked = sorted(personal_by_url.values(), key=lambda item: item[0], reverse=True)
    selected = ranked[:MAX_ITEMS]
    selected_people = personal_ranked[:3] if p == "linkedin" else []
    if selected or selected_people:
        method = selected[0][3] if selected else selected_people[0][3]
        print(
            f"Native social discovery ranking: platform={p}; method={method}; "
            f"company_candidates={len(ranked)}; selected_for_enrichment={len(selected)}; "
            f"personal_account_candidates={len(selected_people)}"
        )
    elif last_error:
        print(
            f"Social discovery ended without candidates: platform={p}; "
            f"last_error={type(last_error).__name__}: {last_error}"
        )
    print(f"SOCIAL_ONLY_GUARD platform={p}; external_web_search_used=false; maps_used=false")
    return (
        [(url, query, method) for score, url, query, method in selected]
        + [(url, query, method) for score, url, query, method in selected_people]
    )

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
    if p not in ("linkedin", "facebook"):
        return "", None, [f"unsupported_platform:{p}"]

    # Profile enrichment is restricted to social-network-specific endpoints.
    # No Cloudflare browser, Google/Maps, Firecrawl, Exa, or generic web fallback.
    providers = []
    if SCRAPECREATORS:
        providers.append(("ScrapeCreators", lambda: scrapecreators_profile(p, url)))
    if p == "linkedin" and APIFY:
        providers.append(("Apify", lambda: apify_profile(p, url)))
    if not providers:
        errors.append("no_profile_provider_available_or_worker_cap_reached")
        if p == "linkedin":
            indexed = LINKEDIN_SEARCH_RESULTS.get(url.rstrip("/"), {})
            title = val(indexed.get("current_company") or indexed.get("title"))
            snippet = val(" | ".join(
                val(indexed.get(key)) for key in ("snippet", "headline", "location")
                if val(indexed.get(key))
            ))
            if title or snippet:
                errors.append("using_public_search_metadata_fallback")
                return "SearchDiscoveryFallback", {
                    "name": title or val(indexed.get("title")) or urlparse(url).path.rstrip("/").split("/")[-1],
                    "description": snippet,
                    "location": val(indexed.get("location")),
                    "url": url,
                }, errors
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
                relevant = relevant_social_profile(
                    p, normalized[9] or url, company, web, loc, bio
                )
                strict_relevance = os.getenv("SOCIAL_STRICT_RELEVANCE", "false").strip().lower() in ("1", "true", "yes", "on")
                if not relevant and strict_relevance:
                    errors.append(f"{name}:profile_not_relevant_to_target")
                    print(
                        f"Social provider response rejected as irrelevant: {name}; "
                        f"platform={p}; fields={json.dumps(extracted, ensure_ascii=False)}"
                    )
                    if name == "ScrapeCreators":
                        return "", None, errors
                    continue
                if not relevant:
                    print(
                        f"SOCIAL_RELEVANCE_OBSERVATION provider={name}; platform={p}; "
                        "strict_filter=disabled; candidate_retained_for_observation"
                    )
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

    # Preserve public search evidence when enrichment providers fail. This is a
    # discovery fallback, not proof of buyer fit; only explicitly published
    # contact details found in the snippet may be used for outreach.
    if p == "linkedin":
        indexed = LINKEDIN_SEARCH_RESULTS.get(url.rstrip("/"), {})
        title = val(indexed.get("current_company") or indexed.get("title"))
        snippet = val(" | ".join(
            val(indexed.get(key)) for key in ("snippet", "headline", "location")
            if val(indexed.get(key))
        ))
        if title or snippet:
            errors.append("using_public_search_metadata_fallback")
            return "SearchDiscoveryFallback", {
                "name": title or val(indexed.get("title")) or urlparse(url).path.rstrip("/").split("/")[-1],
                "description": snippet,
                "location": val(indexed.get("location")),
                "url": url,
            }, errors
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
    # Some Worker responses wrap profile fields as JSON/text strings instead of
    # nested objects. Decode JSON wrappers before walking the response.
    def decode_wrappers(value, depth=0):
        if depth > 5:
            return value
        if isinstance(value, dict):
            decoded = {}
            for key, child in value.items():
                if isinstance(child, str) and child.strip().startswith(("{", "[")):
                    try:
                        child = json.loads(child)
                    except (TypeError, ValueError):
                        pass
                decoded[key] = decode_wrappers(child, depth + 1) if isinstance(child, (dict, list)) else child
            return decoded
        if isinstance(value, list):
            return [decode_wrappers(child, depth + 1) if isinstance(child, (dict, list)) else child for child in value]
        return value

    data = decode_wrappers(data)
    nodes = list(_profile_dicts(data))
    name = _first_profile_value(nodes, (
        "name", "pageName", "page_name", "displayName", "display_name",
        "title", "fullName", "full_name", "username", "handle",
        "company", "companyName", "company_name", "organization"
    ))
    website = _first_profile_value(nodes, (
        "website", "websiteUrl", "website_url", "externalUrl", "external_url",
        "link", "web_url", "companyWebsite", "company_website", "domain"
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
        "page_intro", "headline", "category", "services", "content", "text",
        "markdown", "pageContent", "page_content", "rawText", "raw_text",
        "summary", "snippet"
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

    if provider == "SearchDiscoveryFallback":
        x = data if isinstance(data, dict) else {}
        name = val(x.get("name"))
        bio = val(x.get("description") or x.get("snippet") or x.get("content"))
        email = _first_profile_value([x], ("email", "public_email", "contact_email"))
        phone = _first_profile_value([x], ("phone", "phone_number", "contact_phone"))
        website = val(x.get("website"))
        location = val(x.get("location"))
        return name, website, location, name, email, phone, bio, "", "", val(x.get("url")) or fallback

    if isinstance(data, dict):
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
        "cloudflare_primary_discovery": False,
        "collection_scope": "linkedin_facebook_only",
        "generic_web_search_enabled": False,
        "google_maps_enabled": False,
        "provider_counts": provider_counts,
        "cloudflare": {
            "token_used": False,
            "calls_used": 0,
            "requests": []
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



def qualify_product_lead_with_ai(row):
    """Use the already-configured Gemini integration to assess B2B customer fit.

    This only classifies public evidence; it never contacts a prospect or invents
    missing facts. A failed/missing AI provider leaves the lead pending review.
    """
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        print("GEMINI_PRODUCT_QUALIFICATION skipped: GEMINI_API_KEY missing")
        return {
            "ai_customer_fit": "unknown",
            "ai_confidence": "0",
            "ai_recommended_buyer_type": "",
            "ai_reason": "AI key not configured; manual review required",
            "ai_review_status": "pending_manual_review",
        }
    try:
        from gemini_utils import generate_text
        prompt = f"""You qualify potential B2B customers for an international trade company.
Assess whether this organization is plausibly a real business customer for the specified product in the specified market.
Be strict about buyer versus competitor. If an organization primarily manufactures, brands, markets, or sells the same target product, classify it as supplier_only even if it imports or distributes products; only classify it as a customer when evidence supports buying third-party products for resale or consuming the product in its own operations. A brand affiliate or product seller is not a target customer without explicit procurement/buying evidence.
Distinguish likely buyers, possible buyers, suppliers/competitors, and irrelevant organizations.
Use only the evidence below. If evidence is thin or only a search snippet, say so. Never invent contact details or company facts.
Target product: {row.get('product_interest', '')}
Target country/market: {row.get('country', '')}
Buyer types of interest: {row.get('buyer_type', '')}
Company/result title: {row.get('company_name', '')}
Website: {row.get('website', '')}
Source URL: {row.get('source_url', '')}
Public evidence: {row.get('evidence', '')[:2200]}

Return ONLY valid JSON with these keys:
{{"fit":"likely_customer|possible_customer|supplier_only|irrelevant|uncertain","confidence":0,"recommended_buyer_type":"short label or empty","reason":"one concise evidence-based sentence","needs_manual_review":true}}
Confidence must be an integer from 0 to 100. Set needs_manual_review true whenever evidence is incomplete.
"""
        model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()
        raw = generate_text(prompt, api_key=key, model=model)
        match = re.search(r"\{[\s\S]*\}", raw or "")
        if not match:
            raise ValueError("Gemini response was not JSON")
        result = json.loads(match.group(0))
        allowed = {"likely_customer", "possible_customer", "supplier_only", "irrelevant", "uncertain"}
        fit = val(result.get("fit")).lower()
        if fit not in allowed:
            fit = "uncertain"
        try:
            confidence = max(0, min(100, int(result.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0
        reason = val(result.get("reason"))[:500]
        review = bool(result.get("needs_manual_review", True)) or confidence < 75 or fit in {"uncertain", "possible_customer"}
        return {
            "ai_customer_fit": fit,
            "ai_confidence": str(confidence),
            "ai_recommended_buyer_type": val(result.get("recommended_buyer_type"))[:120],
            "ai_reason": reason or "AI provided no explanation; manual review required",
            "ai_review_status": "pending_manual_review" if review else "ai_screened_review_before_outreach",
        }
    except Exception as exc:
        print(f"GEMINI_PRODUCT_QUALIFICATION failed safely: {type(exc).__name__}")
        return {
            "ai_customer_fit": "unknown",
            "ai_confidence": "0",
            "ai_recommended_buyer_type": "",
            "ai_reason": "AI qualification failed; manual review required",
            "ai_review_status": "pending_manual_review",
        }


def run_product_web_collection():
    """Collect product prospects through Composio, capped Cloudflare web/Maps, and Firecrawl fallback.

    Scheduled discovery is paced to every third workflow run. Cloudflare's own
    Worker budget is four Browser actions/day; direct Firecrawl remains optional
    and is never required when Composio or Cloudflare can return public results.
    """
    global FIRECRAWL_SEARCHES_THIS_RUN, FIRECRAWL_SCRAPES_THIS_RUN

    rows = []
    if PRODUCT_LEADS.exists():
        with PRODUCT_LEADS.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

    # Clean supplier-only and irrelevant records from earlier runs so they do not
    # remain in the active customer list or block future discovery by domain.
    prior_count = len(rows)
    rows = [
        row for row in rows
        if val(row.get("ai_customer_fit")).lower() not in {"supplier_only", "irrelevant"}
    ]
    removed_prior = prior_count - len(rows)
    if removed_prior:
        print(f"AI_EXISTING_LEADS_CLEANUP removed_non_customer_records={removed_prior}")

    seen_domains = {
        domain(val(row.get("website")))
        for row in rows if val(row.get("website"))
    }
    seen_source_urls = {
        val(row.get("source_url")).split("#", 1)[0].rstrip("/")
        for row in rows if val(row.get("source_url"))
    }

    # Search for buyers with a country-localized query.
    # All three dimensions are driven by manual workflow inputs.
    query = (
        f'{TARGET_MARKET} "{PRODUCT_NAME}" {BUYER_TYPES} '
        'buyers importers distributors factories procurement industrial end users company contact'
    )

    # The workflow checks every three hours, but external discovery is paced to
    # every third scheduled run to preserve the shared external-provider budgets.
    event_name = os.getenv("GITHUB_EVENT_NAME", "").strip().lower()
    if event_name == "schedule" and GITHUB_RUN_NUMBER % 3 != 0:
        PRODUCT_LEADS.parent.mkdir(parents=True, exist_ok=True)
        with PRODUCT_LEADS.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PRODUCT_LEAD_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(
            f"FIRECRAWL_PRODUCT_SEARCH skipped: scheduled_budget_pacing; "
            f"run_number={GITHUB_RUN_NUMBER}; next_discovery_run=multiple_of_3"
        )
        return

    search_country = firecrawl_country_code(TARGET_MARKET)

    def find_result_items(node):
        if isinstance(node, list):
            direct = [item for item in node if isinstance(item, dict) and val(item.get("url"))]
            if direct:
                return direct
            for child in node:
                found = find_result_items(child)
                if found:
                    return found
        elif isinstance(node, dict):
            for key in ("web", "results", "items", "organic", "searchResults", "data"):
                child = node.get(key)
                found = find_result_items(child)
                if found:
                    return found
            for child in node.values():
                if isinstance(child, (dict, list)):
                    found = find_result_items(child)
                    if found:
                        return found
        return []

    # Composio is the primary discovery route. Direct Firecrawl is the bounded
    # fallback when the Composio project has no usable Firecrawl connection/results.
    payload = {}
    items = []
    search_provider = "Composio Firecrawl"
    composio_error = ""
    if COMPOSIO_API_KEY:
        try:
            account = _resolve_composio_firecrawl_account()
            composio_response = session.post(
                COMPOSIO_EXECUTE_URL,
                headers={"x-api-key": COMPOSIO_API_KEY, "Accept": "application/json", "Content-Type": "application/json"},
                json={
                    "user_id": account["user_id"],
                    "connected_account_id": account["id"],
                    "arguments": {
                        **{
                            "q": query,
                            "limit": 5,
                            "lang": "en",
                            "formats": ["links"],
                        },
                        **({"country": search_country} if search_country else {}),
                    },
                },
                timeout=75,
            )
            composio_response.raise_for_status()
            payload = composio_response.json()
            if isinstance(payload, dict) and payload.get("successful") is False:
                raise RuntimeError("Composio Firecrawl Search returned successful=false")
            items = find_result_items(payload.get("data") or payload)[:max(1, MAX_ITEMS - 1)]
            print(f"COMPOSIO_PRODUCT_SEARCH results={len(items)}; reserved_slot_for_cloudflare={bool(CLOUDFLARE_TOKEN)}")
        except Exception as exc:
            composio_error = f"{type(exc).__name__}: {exc}"
            print(f"COMPOSIO_PRODUCT_SEARCH failed; trying bounded direct Firecrawl fallback: {composio_error}")
            items = []

    # Cloudflare Browser Run also collects public web and Google Maps results.
    # The Worker enforces four browser actions/day and an 11-second minimum interval.
    # Alternate sources on discovery runs so they fit within the shared action budget.
    if len(items) < MAX_ITEMS and CLOUDFLARE_TOKEN and CLOUDFLARE_CALLS_THIS_RUN < MAX_CLOUDFLARE_CALLS_PER_RUN:
        cloudflare_platform = "maps" if GITHUB_RUN_NUMBER % 2 == 0 else "web"
        cloudflare_query = (
            f'{TARGET_MARKET} "{PRODUCT_NAME}" {BUYER_TYPES} '
            + ("businesses companies buyers importers distributors Google Maps" if cloudflare_platform == "maps"
               else "official company websites buyers importers distributors procurement contact")
        )
        try:
            cloudflare_items = cloudflare_public_web_candidates(cloudflare_query, cloudflare_platform)
            had_primary_items = bool(items)
            existing_candidate_urls = {
                val(item.get("url")).split("#", 1)[0].rstrip("/")
                for item in items if isinstance(item, dict) and val(item.get("url"))
            }
            for cloudflare_item in cloudflare_items:
                cloudflare_url = val(cloudflare_item.get("url")).split("#", 1)[0].rstrip("/")
                if cloudflare_url and cloudflare_url not in existing_candidate_urls:
                    items.append(cloudflare_item)
                    existing_candidate_urls.add(cloudflare_url)
                if len(items) >= MAX_ITEMS:
                    break
            if cloudflare_items and not had_primary_items:
                search_provider = "Cloudflare Google Maps Search" if cloudflare_platform == "maps" else "Cloudflare Public Web Search"
        except Exception as exc:
            print(f"CLOUDFLARE_PUBLIC_SEARCH failed safely: {type(exc).__name__}: {str(exc)[:180]}")

    if not items and FIRECRAWL_API_KEY:
        search_provider = "Direct Firecrawl fallback"
        if FIRECRAWL_SEARCHES_THIS_RUN >= MAX_FIRECRAWL_SEARCHES_PER_RUN:
            print("FIRECRAWL_PRODUCT_SEARCH blocked: per_run_cap_reached")
        else:
            FIRECRAWL_SEARCHES_THIS_RUN += 1
            response = session.post(
                "https://api.firecrawl.dev/v2/search",
                headers={
                    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    **{"query": query, "limit": 5, "lang": "en"},
                    **({"country": search_country} if search_country else {}),
                },
                timeout=45,
            )
            if response.status_code == 402:
                print("FIRECRAWL_PRODUCT_SEARCH stopped: credit_limit_reached_http_402; no retries")
                response = None
            if response is not None:
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("success") is False:
                    raise RuntimeError("Firecrawl product search returned an unsuccessful response")
                items = find_result_items(payload.get("data") or payload.get("results") or payload)
    elif not items:
        search_provider = "No usable search provider"
        print("PRODUCT_WEB_DISCOVERY_NO_RESULTS: Composio and Cloudflare returned no candidates; direct Firecrawl key not configured")

    # Use the already-configured Exa key as a bounded secondary discovery source.
    # Its shared monthly counter caps this fallback at 20 searches across modes.
    exa_candidates_added = 0
    if EXA_API_KEY and len(items) < MAX_ITEMS and _reserve_exa_search_call():
        exa_query = (
            f'B2B customers in {TARGET_MARKET} that buy or use "{PRODUCT_NAME}". '
            f'Prioritize {BUYER_TYPES}, procurement teams, and industrial end-users. '
            'Find actual companies and official websites, not market reports or product suppliers.'
        )
        try:
            exa_response = session.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": EXA_API_KEY,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "query": exa_query,
                    "type": "auto",
                    "numResults": 5,
                    "contents": {"highlights": True},
                },
                timeout=25,
            )
            exa_response.raise_for_status()
            exa_payload = exa_response.json()
            existing_urls = {
                val(item.get("url")).split("#", 1)[0].rstrip("/")
                for item in items if isinstance(item, dict) and val(item.get("url"))
            }
            for result in (exa_payload.get("results") or []):
                if not isinstance(result, dict):
                    continue
                result_url = val(result.get("url")).split("#", 1)[0].rstrip("/")
                if not result_url or result_url in existing_urls:
                    continue
                highlights = result.get("highlights") or []
                if isinstance(highlights, list):
                    description = " ".join(val(value) for value in highlights if val(value))
                else:
                    description = val(highlights)
                description = description or val(result.get("text") or result.get("summary"))
                items.append({
                    "url": result_url,
                    "title": val(result.get("title")),
                    "description": description,
                    "content": description,
                    "search_provider": "Exa Search API",
                })
                existing_urls.add(result_url)
                exa_candidates_added += 1
            print(
                f"EXA_PRODUCT_SEARCH results={len(exa_payload.get('results') or [])}; "
                f"unique_candidates_added={exa_candidates_added}"
            )
        except Exception as exc:
            print(f"EXA_PRODUCT_SEARCH failed safely: {type(exc).__name__}")

    email_re = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
    phone_re = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)")

    def plausible_public_phone(value):
        raw_value = val(value).strip()
        if re.fullmatch(r"(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", raw_value):
            return False
        if re.fullmatch(r"\d{1,2}[-/.][A-Za-z]{3}[-/.]\d{2,4}", raw_value):
            return False
        digits = re.sub(r"\D", "", raw_value)
        if not 8 <= len(digits) <= 15 or len(set(digits)) <= 2:
            return False
        # Drop common placeholder/test numbers frequently embedded in pages.
        if any(pattern in digits for pattern in (
            "1234567890", "0123456789", "123123123", "00000000", "11111111", "99999999"
        )):
            return False
        return True
    excluded_hosts = (
        "facebook.com", "linkedin.com", "instagram.com", "tiktok.com",
        "youtube.com", "x.com", "twitter.com", "wikipedia.org",
        "crunchbase.com", "zoominfo.com", "apollo.io", "yellowpages.com",
    )
    added = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        url = val(item.get("url")).split("#", 1)[0].rstrip("/")
        host = domain(url)
        is_google_maps = bool(item.get("is_google_maps")) or (
            "google." in host and "/maps/" in urlparse(url).path.lower()
        )
        if not url.startswith(("https://", "http://")) or not host:
            continue
        if url in seen_source_urls:
            continue
        if any(host == blocked or host.endswith("." + blocked) for blocked in excluded_hosts):
            continue
        title = val(item.get("title"))
        path = urlparse(url).path.lower()
        blob = " ".join(val(item.get(k)) for k in ("title", "description", "markdown", "content"))
        combined_text = (title + " " + blob).lower()
        # Reject obvious listicles, directories, and market-research pages before
        # spending an AI classification call on results that cannot be buyers.
        if any(signal in combined_text for signal in (
            "top 10", "best 10", "supplier directory", "company directory",
            "list of suppliers", "market size", "market report", "market research",
            "market forecast", "market share", "market trends", "industry report",
            "research report", "market outlook", "market growth", "market analysis",
        )):
            print(f"PRODUCT_RESULT_REJECTED reason=directory_or_market_report; title={title[:160]}")
            continue
        if any(signal in path for signal in ("/blog/", "/blogs/", "/category/", "/tag/", "/directory/", "/listing/")):
            continue
        website = "" if is_google_maps else f"{urlparse(url).scheme}://{host}"
        if website and host in seen_domains:
            continue

        # Only scrape one official-site candidate every third workflow run.
        # Search-only runs cost about 2 credits; this averages ~2.33 credits/run.
        scrape_attempted = False
        scrape_succeeded = False
        official_page_text = ""
        if GITHUB_RUN_NUMBER > 0 and GITHUB_RUN_NUMBER % 9 == 0 and FIRECRAWL_SCRAPES_THIS_RUN < MAX_FIRECRAWL_SCRAPES_PER_RUN:
            FIRECRAWL_SCRAPES_THIS_RUN += 1
            scrape_attempted = True
            try:
                scrape_response = session.post(
                    "https://api.firecrawl.dev/v2/scrape",
                    headers={
                        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                    timeout=45,
                )
                if scrape_response.status_code == 402:
                    print("FIRECRAWL_PRODUCT_SCRAPE skipped: credit_limit_reached_http_402; no retries")
                else:
                    scrape_response.raise_for_status()
                    scrape_payload = scrape_response.json()
                    scrape_data = scrape_payload.get("data") if isinstance(scrape_payload, dict) else {}
                    if isinstance(scrape_data, dict):
                        metadata = scrape_data.get("metadata") or {}
                        source_url = val(metadata.get("sourceURL") or metadata.get("url"))
                        # Do not trust a redirected third-party page as the company's own contact source.
                        if not source_url or domain(source_url) == host:
                            scraped_markdown = val(scrape_data.get("markdown") or scrape_data.get("content"))
                            if scraped_markdown:
                                official_page_text = scraped_markdown
                                blob = (blob + "\n" + scraped_markdown).strip()
                                scrape_succeeded = True
            except Exception as exc:
                print(f"FIRECRAWL_PRODUCT_SCRAPE failed safely: {type(exc).__name__}")
        # Outbound contact details must come from the successfully scraped official site,
        # never only from a search snippet.
        contact_text = official_page_text if scrape_succeeded else blob
        item_emails = sorted(set(email_re.findall(contact_text)))
        item_phones = sorted({
            candidate for candidate in phone_re.findall(contact_text)
            if plausible_public_phone(candidate)
        })
        # Only extract a WhatsApp contact when the official page explicitly labels it
        # as WhatsApp or provides a public wa.me link; a generic phone number is not enough.
        item_whatsapp = ""
        wa_link = re.search(r"(?i)https?://(?:www\.)?wa\.me/(\d{8,15})", official_page_text)
        if wa_link:
            item_whatsapp = "+" + wa_link.group(1)
        else:
            for wa_label in re.finditer(r"(?i)whats\s*app|whatsapp", official_page_text):
                segment = official_page_text[wa_label.end():wa_label.end() + 120]
                wa_phone = re.search(r"(?<!\w)(\+\s?\d[\d ().-]{7,}\d|00\s?\d[\d ().-]{7,}\d)", segment)
                if wa_phone:
                    raw_wa = re.sub(r"[ ().-]", "", wa_phone.group(1))
                    item_whatsapp = "+" + raw_wa[2:] if raw_wa.startswith("00") else raw_wa
                    break
        if item_whatsapp and not plausible_public_phone(item_whatsapp):
            item_whatsapp = ""
        title_parts = re.split(r"\s+[|–—-]\s+", title, maxsplit=1)
        company = title_parts[0].strip() or host
        generic_page_titles = {
            "about us", "home", "contact", "contact us", "products",
            "our company", "company profile", "welcome", "who we are",
        }
        if len(title_parts) > 1 and company.lower() in generic_page_titles:
            company = title_parts[1].strip() or host
        row = {
            "company_name": company[:200],
            "website": website,
            "country": TARGET_MARKET,
            "buyer_type": BUYER_TYPES,
            "product_interest": PRODUCT_NAME,
            "email": item_emails[0] if item_emails else "",
            "phone": item_phones[0] if item_phones else "",
            "whatsapp": item_whatsapp,
            "source_url": url,
            "evidence": blob[:2500],
            "verification_status": "official_domain_scraped_needs_review" if scrape_succeeded else "search_result_only_needs_review",
            "ai_customer_fit": "unknown",
            "ai_confidence": "0",
            "ai_recommended_buyer_type": "",
            "ai_reason": "",
            "ai_review_status": "pending_manual_review",
            "run_id": RUN_ID,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        row.update(qualify_product_lead_with_ai(row))
        # Use the AI's specific classification when available; otherwise keep the
        # product-derived targeting profile and never invent a named buyer role.
        row["buyer_type"] = row.get("ai_recommended_buyer_type") or BUYER_TYPES
        if row.get("ai_customer_fit") in {"supplier_only", "irrelevant"}:
            # Temporary observation mode: retain AI-labelled candidates in the
            # dataset so we can inspect real collection results before restoring
            # qualification gates one at a time.
            print(
                "AI_PRODUCT_LEAD_LABEL_ONLY " + json.dumps({
                    "company": company[:160],
                    "fit": row.get("ai_customer_fit"),
                    "confidence": row.get("ai_confidence"),
                    "reason": row.get("ai_reason", "")[:300],
                    "source_url": url,
                }, ensure_ascii=False)
            )
        rows.append(row)
        if website:
            seen_domains.add(host)
        seen_source_urls.add(url)
        added += 1
        if added >= MAX_ITEMS:
            break

    PRODUCT_LEADS.parent.mkdir(parents=True, exist_ok=True)
    with PRODUCT_LEADS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PRODUCT_LEAD_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(
        "FIRECRAWL_PRODUCT_RUN " + json.dumps({
            "mode": "product_web",
            "search_provider": search_provider,
            "composio_fallback_reason": composio_error,
            "searches_used": FIRECRAWL_SEARCHES_THIS_RUN,
            "per_run_search_cap": MAX_FIRECRAWL_SEARCHES_PER_RUN,
            "search_result_count": len(items),
            "exa_candidates_added": exa_candidates_added,
            "response_keys": sorted(payload.keys()),
            "result_payload_type": type(payload.get("data")).__name__,
            "result_container_keys": sorted(payload.get("data").keys()) if isinstance(payload.get("data"), dict) else [],
            "new_company_candidates": added,
            "total_saved_candidates": len(rows),
            "credits_estimate_for_search": 2 if search_provider in ("Composio Firecrawl", "Direct Firecrawl fallback") and (items or payload.get("success")) else 0,
            "scrape_calls": FIRECRAWL_SCRAPES_THIS_RUN,
            "scrape_per_run_cap": MAX_FIRECRAWL_SCRAPES_PER_RUN,
            "workflow_run_number": GITHUB_RUN_NUMBER,
            "outreach_sent": False,
            "file": str(PRODUCT_LEADS.relative_to(ROOT)) if PRODUCT_LEADS.is_relative_to(ROOT) else PRODUCT_LEADS.name,
        }, ensure_ascii=False),
        flush=True,
    )


def main():
    # This repository is intentionally social-only. Fail closed if an old
    # workflow or environment variable attempts to invoke the former web mode.
    if COLLECTION_MODE != "social":
        print(
            f"COLLECTION_MODE_BLOCKED mode={COLLECTION_MODE!r}; "
            "this repository supports only LinkedIn/Facebook social collection."
        )
        return

    # Google Sheets is the single durable database. Fail closed before any
    # provider calls if its endpoint/token are not configured.
    store = GoogleSheetStore.from_env()
    rows = store.read_table("Leads")
    fields = list(FIELDS)
    for stored in rows:
        for field in stored.keys():
            if field not in fields:
                fields.append(field)

    # Keep an ephemeral runner-local CSV cache only for downstream compatibility;
    # it is not committed or uploaded as an artifact.
    with LEADS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    profile_rows = store.read_table("Personal Profiles")
    with PERSONAL_PROFILES.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PERSONAL_PROFILE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(profile_rows)

    # Read durable state BEFORE discovery on every run. Known company pages and
    # previously saved personal accounts are excluded before profile enrichment.

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
        f"EXISTING_STATE_LOADED sheet_leads_rows={len(rows)}; "
        f"sheet_personal_profile_rows={len(profile_rows)}; "
        f"company_social_keys={len(EXISTING_SOCIAL_KEYS)}; "
        f"personal_profile_keys={len(EXISTING_PERSONAL_KEYS)}"
    )
    if not isinstance(rows, list) or not isinstance(profile_rows, list):
        raise RuntimeError("Google Sheets read returned an invalid row collection; stopping before social providers.")
    print(
        "PRODUCT_PROFILE " + json.dumps({
            "selected_product": PRODUCT_NAME,
            "target_market": TARGET_MARKET,
            "inferred_buyer_types": BUYER_TYPES,
            "product_terms_count": len(PRODUCT_TERMS),
            "product_terms_sample": list(PRODUCT_TERMS[:12])
        }, ensure_ascii=False)
    )

    # Search only within the requested social networks using native provider APIs.
    # Do not call Composio Firecrawl, Google, Exa, Cloudflare /search, or Maps.
    is_auto = PLATFORM not in PLATFORMS
    platforms_to_collect = AUTO_PLATFORMS if is_auto else [PLATFORM]
    for target in ("linkedin", "facebook"):
        COMPOSIO_PRIMARY_RESULTS[target] = []
        CLOUDFLARE_PRIMARY_RESULTS[target] = []
    print(
        "SOCIAL_ONLY_MODE enabled=true; "
        "platforms=linkedin,facebook; generic_web_search=false; google_maps=false"
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
            {"ScrapeCreators": 0, "Apify": 0, "failed": 0},
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
        "ScrapeCreators": 0,
        "Apify": 0,
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

        relevant = relevant_social_profile(p, surl or url, company, web, loc, bio)
        strict_relevance = os.getenv("SOCIAL_STRICT_RELEVANCE", "false").strip().lower() in ("1", "true", "yes", "on")
        if not relevant and strict_relevance:
            profile_audit_row["outcome"] = "rejected_not_relevant"
            print(f"Rejected low-confidence social result: platform={p}; discovered_url={url}; returned_profile_url={returned_url or 'NOT_RETURNED'}")
            continue
        profile_audit_row["outcome"] = "accepted_relevant" if relevant else "retained_observation_mode"
        if not relevant:
            print(f"SOCIAL_RELEVANCE_OBSERVATION platform={p}; strict_filter=disabled; candidate_retained_for_observation; url={url}")

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

    # Persist all lead records and account-only profiles to Google Sheets.
    # The upsert preserves non-empty existing values and avoids duplicate keys.
    store.upsert_table("Leads", rows, fields, key_column="social_url")
    with PERSONAL_PROFILES.open(encoding="utf-8", newline="") as handle:
        profile_rows = list(csv.DictReader(handle))
    store.upsert_table(
        "Personal Profiles", profile_rows, PERSONAL_PROFILE_FIELDS,
        key_column="profile_url"
    )

    print(
        "GOOGLE_SHEETS_PERSISTENCE completed=true; tabs=Leads,Personal Profiles; local_csv_is_ephemeral=true"
    )
    print(
        "SOCIAL_ONLY_PROVIDER_STATUS supported_platforms=linkedin,facebook; "
        "generic_web_search=false; google_maps=false; cloudflare_search=false; "
        "firecrawl=false; exa=false; composio_firecrawl=false"
    )
    print(
        f"Social collection: platforms={','.join(platforms_to_collect)}; "
        f"discovered_candidates={len(found)}; added={added}; target_cap={MAX_ITEMS}; "
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
