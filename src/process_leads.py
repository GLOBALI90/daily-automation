import csv
import json
import os
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
COMPANY = json.loads((ROOT / "config/company.json").read_text(encoding="utf-8"))
LEADS = ROOT / "data/leads.csv"
OUTREACH = ROOT / "data/outreach.csv"

OUTREACH_LIMIT_PER_RUN = 20
AUTH_USERNAME = "saberi.export.import@gmail.com"
FROM_ADDRESS = "saberi.export.import@gmail.com"


def llm(prompt):
    providers = [
        ("GEMINI_API_KEY", os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai"), os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")),
        ("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"), os.getenv("OPENROUTER_MODEL", "")),
    ]
    for secret, base, model in providers:
        key = os.getenv(secret)
        if not key or not model:
            continue
        try:
            r = requests.post(
                base.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.3, "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            print(f"LLM provider {secret} failed: {exc}")
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
Company: {row.get('company_name','')}
Country: {row.get('country','')}
Industry: {row.get('industry','')}
Potential product interest: {row.get('product_interest','')}
Evidence: {row.get('evidence','')}
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
    done = {r.get("website") for r in existing if r.get("website")}
    current_run_id = os.getenv("GITHUB_RUN_ID", "")
    if current_run_id:
        candidates = [r for r in rows if r.get("run_id") == current_run_id]
    else:
        candidates = rows[-OUTREACH_LIMIT_PER_RUN:]

    fieldnames = ["company_name", "website", "email", "message", "status", "run_id"]
    new_rows = []

    for row in candidates[:OUTREACH_LIMIT_PER_RUN]:
        if row.get("website") in done:
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
        done.add(row.get("website"))

    all_rows = existing + new_rows
    with OUTREACH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    sent_count = sum(1 for r in new_rows if r.get("status") == "sent")
    failed_count = sum(1 for r in new_rows if r.get("status", "").startswith("send_failed"))
    no_email_count = sum(1 for r in new_rows if r.get("status") == "no_public_email")
    print(f"Generated {len(new_rows)} outreach records; sent {sent_count} emails; send failures {failed_count}; no public email {no_email_count}")


if __name__ == "__main__":
    main()
