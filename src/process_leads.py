import csv
import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
COMPANY = json.loads((ROOT / "config/company.json").read_text(encoding="utf-8"))
LEADS = ROOT / "data/leads.csv"
OUTREACH = ROOT / "data/outreach.csv"


def llm(prompt):
    providers = [
        ("GROQ_API_KEY", os.getenv("GROQ_API_BASE", "https://api.groq.com/openai/v1"), os.getenv("GROQ_MODEL", "")),
        ("GEMINI_API_KEY", os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai"), os.getenv("GEMINI_MODEL", "")),
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
    prompt = f"""You are the B2B trade development assistant for {COMPANY['brand']} ({COMPANY['legal_name']}).
Business: sourcing and international trade in petroleum products, chemicals, petrochemicals, steel and renewable energy.
Website: {COMPANY['website']}
WhatsApp: {COMPANY['whatsapp']}
Create one concise professional B2B introduction email for this potential buyer.
Use only the facts supplied below. Do not invent products, volumes, names, prices or contact details.
Company: {row.get('company_name','')}
Industry: {row.get('industry','')}
Potential product interest: {row.get('product_interest','')}
Evidence: {row.get('evidence','')}
Return only: SUBJECT: ... then the email body.
"""
    return llm(prompt)


def send_email(to, content):
    if os.getenv("SEND_EMAILS", "false").lower() != "true":
        return "draft_only"
    host, port = os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587"))
    user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", user)
    if not all([host, user, password, sender, to]):
        return "missing_smtp_config"
    subject = "ROZHAN GLOBAL — International Supply Cooperation"
    body = content
    if content.startswith("SUBJECT:"):
        lines = content.splitlines()
        subject = lines[0].replace("SUBJECT:", "").strip() or subject
        body = "\n".join(lines[1:]).strip()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    return "sent"


def main():
    if not LEADS.exists():
        print("No leads file found")
        return
    rows = list(csv.DictReader(LEADS.open(encoding="utf-8")))
    existing = list(csv.DictReader(OUTREACH.open(encoding="utf-8"))) if OUTREACH.exists() else []
    done = {r.get("website") for r in existing}
    fieldnames = ["company_name", "website", "email", "message", "status"]
    new_rows = []
    for row in rows[-10:]:
        if row.get("website") in done:
            continue
        message = make_message(row)
        if not message:
            continue
        status = send_email(row.get("email", ""), message) if row.get("email") else "no_public_email"
        new_rows.append({"company_name": row.get("company_name", ""), "website": row.get("website", ""), "email": row.get("email", ""), "message": message, "status": status})
    all_rows = existing + new_rows
    with OUTREACH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Generated {len(new_rows)} outreach records")


if __name__ == "__main__":
    main()
