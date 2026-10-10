import csv
import json
import os
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

import requests
from google import genai

ROOT = Path(__file__).resolve().parents[1]
COMPANY = json.loads((ROOT / "config/company.json").read_text(encoding="utf-8"))
LEADS = ROOT / "data/leads.csv"
OUTREACH = ROOT / "data/outreach.csv"

OUTREACH_LIMIT_PER_RUN = 20
AUTH_USERNAME = "saberi.export.import@gmail.com"
FROM_ADDRESS = "saberi.export.import@gmail.com"


def llm(prompt):
    key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    if not key:
        print("Gemini provider skipped: GEMINI_API_KEY is not configured")
        return ""
    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content(model=model, contents=prompt)
        text = (response.text or "").strip()
        if not text:
            print("Gemini provider failed: empty response")
            return ""
        return text
    except Exception as exc:
        print(f"Gemini provider failed: {type(exc).__name__}: {exc}")
        return ""


def make_message(row):
    prompt = f"""You are the senior B2B trade development assistant for {COMPANY['brand']} ({COMPANY['legal_name']}).
Business: international sourcing and trade in petroleum products, chemicals, petrochemicals, steel and renewable energy.
Website: {COMPANY['website']}
WhatsApp: {COMPANY['whatsapp']}

Write one concise, high-quality B2B cold-introduction email for this potential buyer.
The goal is to start a commercial conversation, not to make unsupported claims.

Core positioning to emphasize when relevant:
- competitive international sourcing and supply
- potential to help reduce total procurement cost through competitive sourcing, supplier comparison and cross-border trade
- ability to help solve supply-chain problems such as sourcing difficulty, supplier availability, procurement lead time, continuity of supply and coordination
- reliable communication and willingness to discuss the buyer's current requirements

PRICE POSITIONING RULES:
- Present ROZHAN GLOBAL as offering competitive / market-competitive pricing and cost-saving potential.
- Never claim “lowest price in the world”, “cheapest supplier”, guaranteed savings, or a fixed percentage saving unless that exact fact is present in the supplied evidence.
- Do not invent prices, discounts, volumes, certifications, inventory, factories, contracts, delivery times, or supplier relationships.

PERSONALIZATION RULES:
- Tailor the opening and value proposition to the company's industry, product interest, location and evidence.
- For steel-related buyers, focus on steel/raw-material procurement and production supply needs when supported by evidence.
- For petrochemical/chemical buyers, focus on relevant feedstocks, industrial chemicals or petrochemical supply only when supported by evidence.
- Mention only products that are supported by the row or by the company's stated business; do not force a product into an irrelevant lead.
- Focus on business outcomes: lower procurement cost, alternative sourcing, supply continuity and reduced sourcing friction.
- Keep the email professional, natural and direct. Avoid exaggerated marketing language.
- Use a clear, low-friction CTA such as asking whether they have a current or upcoming requirement and offering to review specifications.

Use only the facts supplied below.
Company / page name: {row.get('company_name','')}
Website: {row.get('website','')}
Public social profile URL: {row.get('social_url','')}
Public contact person: {row.get('contact_person','')}
Country: {row.get('country','')}
Industry: {row.get('industry','')}
Potential product interest: {row.get('product_interest','')}
Public profile description: {row.get('social_bio','')}
Public evidence: {row.get('evidence','')}
Discovery source: {row.get('source','')}
Search query: {row.get('search_query','')}

Return only:
SUBJECT: ...

Email body
"""
    return llm(prompt)


def send_email(to, content, attempts=2):
    if os.getenv("SEND_EMAILS", "false").lower() != "true":
        return "draft_only"
    password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not all([password, to]):
        return "missing_gmail_config"
    subject = "ROZHAN GLOBAL — International Supply Cooperation"
    body = content
    if content.startswith("SUBJECT:"):
        lines = content.splitlines()
        subject = lines[0].replace("SUBJECT:", "").strip() or subject
        body = "\n".join(lines[1:]).strip()

    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            msg = EmailMessage()
            msg["From"] = FROM_ADDRESS
            msg["To"] = to
            msg["Subject"] = subject
            msg["Reply-To"] = FROM_ADDRESS
            msg.set_content(body)
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(AUTH_USERNAME, password)
                smtp.send_message(msg)
            return "sent"
        except (smtplib.SMTPException, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"Email send failed for {to} (attempt {attempt}/{attempts}): {last_error}")
            if attempt < attempts:
                time.sleep(3)
    return f"send_failed: {last_error}" if last_error else "send_failed"


def main():
    if not LEADS.exists():
        print("No leads file found")
        return
    rows = list(csv.DictReader(LEADS.open(encoding="utf-8")))
    existing = list(csv.DictReader(OUTREACH.open(encoding="utf-8"))) if OUTREACH.exists() else []
    def lead_key(row):
        # Prefer stable, specific identifiers. Never use an empty website as a dedupe key.
        for field in ("email", "website", "social_url", "linkedin"):
            value = (row.get(field) or "").strip().lower()
            if value:
                return f"{field}:{value}"
        company = (row.get("company_name") or "").strip().lower()
        country = (row.get("country") or "").strip().lower()
        if company:
            return f"company:{company}|country:{country}"
        return ""

    done = {key for row in existing if (key := lead_key(row))}
    current_run_id = os.getenv("GITHUB_RUN_ID", "")
    if current_run_id:
        candidates = [r for r in rows if r.get("run_id") == current_run_id]
    else:
        candidates = rows[-OUTREACH_LIMIT_PER_RUN:]

    fieldnames = ["company_name", "website", "email", "message", "status", "run_id"]
    new_rows = []

    for row in candidates[:OUTREACH_LIMIT_PER_RUN]:
        key = lead_key(row)
        if key and key in done:
            print(f"Outreach skipped: duplicate lead identity ({key[:120]})")
            continue
        message = make_message(row)
        if not message:
            new_rows.append({
                "company_name": row.get("company_name", ""),
                "website": row.get("website", ""),
                "email": row.get("email", ""),
                "message": "",
                "status": "ai_failed",
                "run_id": row.get("run_id", current_run_id),
            })
            continue
        status = send_email(row.get("email", ""), message) if row.get("email") else "no_public_email"
        new_rows.append({
            "company_name": row.get("company_name", ""),
            "website": row.get("website", ""),
            "email": row.get("email", ""),
            "message": message,
            "status": status,
            "run_id": row.get("run_id", current_run_id),
        })
        if key:
            done.add(key)

    all_rows = existing + new_rows
    with OUTREACH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    sent_count = sum(1 for r in new_rows if r.get("status") == "sent")
    draft_count = sum(1 for r in new_rows if r.get("status") == "draft_only")
    failed_count = sum(1 for r in new_rows if r.get("status", "").startswith("send_failed"))
    no_email_count = sum(1 for r in new_rows if r.get("status") == "no_public_email")
    ai_failed_count = sum(1 for r in new_rows if r.get("status") == "ai_failed")
    print(
        f"Outreach summary: generated={len(new_rows)}; draft_only={draft_count}; "
        f"sent={sent_count}; send_failures={failed_count}; "
        f"no_public_email={no_email_count}; ai_failed={ai_failed_count}; "
        f"send_enabled={os.getenv('SEND_EMAILS', 'false').lower() == 'true'}"
    )


if __name__ == "__main__":
    main()
