import csv, json, os, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/"data"; LEADS=DATA/"leads.csv"; DATA.mkdir(exist_ok=True)
MAX_ITEMS=min(int(os.getenv("SOCIAL_MAX_ITEMS","3")),3)
PLATFORM=os.getenv("SOCIAL_PLATFORM","auto").lower().strip()
APIFY=os.getenv("APIFY_API_TOKEN","").strip(); YDC=os.getenv("YDC_API_KEY","").strip()
RUN_ID=os.getenv("GITHUB_RUN_ID",datetime.now(timezone.utc).strftime("social-%Y%m%d%H%M%S"))
NOW=datetime.now(timezone.utc).isoformat()
PLATFORMS=["linkedin","instagram","facebook","x"]
ACTORS={"linkedin":"harvestapi~linkedin-profile-scraper","instagram":"apify~instagram-profile-scraper","facebook":"apify~facebook-pages-scraper","x":"apidojo~tweet-scraper"}
QUERIES={
"linkedin":['site:linkedin.com/in/ ("procurement manager" OR "purchasing manager" OR "sourcing manager") (petrochemical OR chemical OR steel OR petroleum) China','site:linkedin.com/in/ ("import manager" OR "procurement director" OR "purchase manager") (chemical OR steel OR energy) China'],
"instagram":['site:instagram.com/ ("petrochemical" OR "chemical" OR "steel") China company','site:instagram.com/ ("industrial" OR "manufacturer" OR "importer") China chemical steel'],
"facebook":['site:facebook.com/ "petrochemical" China company','site:facebook.com/ "chemical manufacturer" China'],
"x":['site:x.com/ ("petrochemical" OR "chemical") China company','site:x.com/ ("steel" OR "industrial") China manufacturer']}
FIELDS=["company_name","website","country","industry","buyer_type","product_interest","contact_person","email","whatsapp","phone","linkedin","source","evidence","lead_score","run_id","collected_at","search_query","social_url","platform","username","followers","verified","social_bio","source_provider"]

def domain(u):
    try:return urlparse(u).netloc.lower().replace("www.","")
    except:return ""
def search(q):
    if not YDC: raise RuntimeError("YDC_API_KEY missing")
    r=requests.get("https://api.you.com/v1/search",json={"query":q,"count":10},headers={"X-API-Key":YDC,"Accept":"application/json"},timeout=30); r.raise_for_status()
    return (r.json().get("results") or {}).get("web") or []
def good(p,u):
    d=domain(u); ok={"linkedin":{"linkedin.com"},"instagram":{"instagram.com"},"facebook":{"facebook.com"},"x":{"x.com","twitter.com"}}[p]
    return d in ok and not any(x in u.lower() for x in ["/search","/explore","/hashtag","/jobs"])
def discover(p):
    out=[]; seen=set()
    for q in QUERIES[p]:
        for x in search(q):
            u=(x.get("url") or "").rstrip("/")
            if good(p,u) and u not in seen:
                seen.add(u); out.append((u,q))
                if len(out)>=MAX_ITEMS:return out
    return out
def actor(p,payload):
    if not APIFY: raise RuntimeError("APIFY_API_TOKEN missing")
    r=requests.post(f"https://api.apify.com/v2/acts/{ACTORS[p]}/run-sync-get-dataset-items",headers={"Authorization":f"Bearer {APIFY}","Content-Type":"application/json"},json=payload,timeout=180)
    r.raise_for_status(); data=r.json(); return data if isinstance(data,list) else data.get("items",[])
def scrape(p,urls):
    if p=="linkedin": return actor(p,{"profileScraperMode":"Profile details no email","queries":urls})
    if p=="instagram": return actor(p,{"usernames":[urlparse(u).path.strip("/").split("/")[0] for u in urls]})
    if p=="facebook": return actor(p,{"startUrls":[{"url":u} for u in urls]})
    return actor(p,{"startUrls":urls,"maxItems":MAX_ITEMS,"sort":"Latest"})
def val(x):
    if x is None:return ""
    if isinstance(x,(dict,list)):return json.dumps(x,ensure_ascii=False)
    return str(x).strip()
def norm(p,x,fallback):
    if p=="linkedin":
        name=" ".join(z for z in [val(x.get("firstName")),val(x.get("lastName"))] if z)
        cp=x.get("currentPosition") or []; company=val(cp[0].get("companyName")) if cp and isinstance(cp[0],dict) else ""
        loc=val(((x.get("location") or {}).get("parsed") or {}).get("text")) or val((x.get("location") or {}).get("linkedinText"))
        bio=" | ".join(z for z in [val(x.get("headline")),val(x.get("about")),company,loc] if z)
        return company or name,val((x.get("websites") or [""])[0] if isinstance(x.get("websites"),list) else x.get("websites")),loc,name,"","",bio,val(x.get("followerCount")),val(x.get("verified")),val(x.get("linkedinUrl")) or fallback
    if p=="instagram":
        bio=val(x.get("biography") or x.get("bio")); return val(x.get("fullName") or x.get("name") or x.get("username")),val(x.get("externalUrl") or x.get("website")),val(x.get("location")),val(x.get("fullName") or x.get("username")),"","",bio,val(x.get("followersCount") or x.get("followers")),val(x.get("verified")),val(x.get("url")) or fallback
    if p=="facebook":
        bio=val(x.get("description") or x.get("about")); return val(x.get("name") or x.get("title")),val(x.get("website")),val(x.get("address")),val(x.get("name") or x.get("title")),val(x.get("email")),val(x.get("phone")),bio,val(x.get("followersCount") or x.get("followersText") or x.get("likesCount")),val(x.get("verified")),val(x.get("url") or x.get("input")) or fallback
    u=val(x.get("authorUrl") or x.get("profileUrl") or x.get("url")); name=val(x.get("author") or x.get("authorName") or x.get("username") or x.get("userName")); bio=val(x.get("authorDescription") or x.get("bio")); return name,"",val(x.get("location")),name,val(x.get("email")),val(x.get("phone")),bio,val(x.get("authorFollowers") or x.get("followersCount")),val(x.get("verified")),u or fallback
def main():
    p=PLATFORM if PLATFORM in PLATFORMS else PLATFORMS[datetime.now(timezone.utc).weekday()%4]
    found=discover(p)
    if not found: print(f"No {p} candidates"); return
    items=scrape(p,[u for u,_ in found]); rows=[]; fields=[]
    if LEADS.exists():
        with LEADS.open(encoding="utf-8",newline="") as f: rows=list(csv.DictReader(f)); fields=list(rows[0].keys()) if rows else []
    for f in FIELDS:
        if f not in fields: fields.append(f)
    seen={(r.get("platform",""),r.get("social_url","")) for r in rows if r.get("social_url")}
    added=0
    for i,x in enumerate(items[:MAX_ITEMS]):
        fallback=found[min(i,len(found)-1)][0]; company,web,loc,contact,email,phone,bio,followers,verified,surl=norm(p,x,fallback)
        if (p,surl) in seen: continue
        row={f:"" for f in fields}; row.update({"company_name":company,"website":web,"country":"China","industry":p,"buyer_type":"social_discovery","product_interest":"petroleum products / chemicals / petrochemicals / steel / renewable energy","contact_person":contact,"email":email,"phone":phone,"linkedin":surl if p=="linkedin" else "","source":f"social:{p}","evidence":bio[:3000],"lead_score":str(min(100,25+(20 if web else 0)+(25 if email else 0)+(10 if phone else 0)+(10 if bio else 0))),"run_id":RUN_ID,"collected_at":NOW,"search_query":found[min(i,len(found)-1)][1],"social_url":surl,"platform":p,"username":urlparse(surl).path.strip("/").split("/")[0] if surl else "","followers":followers,"verified":verified,"social_bio":bio[:3000],"source_provider":"Apify"}); rows.append(row); seen.add((p,surl)); added+=1
    with LEADS.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print(f"Social collection: {p}; discovered={len(found)} scraped={len(items)} added={added}; cap={MAX_ITEMS}")
if __name__=="__main__": main()
