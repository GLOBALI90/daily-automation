import csv
import json
import os
import re
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

import requests
from gemini_utils import generate_text

ROOT = Path(__file__).resolve().parents[1]
COMPANY = json.loads((ROOT / "config/company.json").read_text(encoding="utf-8"))
LEADS = ROOT / "data/leads.csv"
OUTREACH = ROOT / "data/outreach.csv"

OUTREACH_LIMIT_PER_RUN = 5
AUTH_USERNAME = "saberi.export.import@gmail.com"
FROM_ADDRESS = "saberi.export.import@gmail.com"


def llm(prompt):
    key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    if not key:
        print("Gemini provider skipped: GEMINI_API_KEY is not configured")
        return ""
    try:
        text = generate_text(prompt, api_key=key, model=model)
        if not text:
            print("Gemini provider failed: empty response")
            return ""
        return text
    except Exception as exc:
        print(f"Gemini provider failed: {type(exc).__name__}: {exc}")
        return ""


def make_message(row):
    prompt = f"""You are the senior B2B trade development assistant for {COMPANY['brand']} ({COMPANY['legal_name']}).
Business: international B2B sourcing and trade focused in this pilot on industrial lubricants, lubricating oils and industrial greases.
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
- For industrial-lubricant companies, focus only on lubricant products, blending/manufacturing capabilities or industrial-grease needs supported by evidence.
- Do not introduce steel, generic chemicals, fuel or unrelated products into this pilot message.
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
    generated = llm(prompt)
    if generated:
        return generated
    # Keep outreach from being blocked by a transient LLM outage. This conservative
    # template mentions only the company's stated trading categories and asks for relevance.
    company = (row.get("company_name") or "your team").strip()
    website = COMPANY.get("website", "")
    return (
        f"SUBJECT: Potential sourcing cooperation with {company}\n\n"
        f"Hello {company} team,\n\n"
        f"I’m reaching out from {COMPANY['brand']} about possible international sourcing cooperation. "
        "Our current pilot focuses on industrial lubricants, lubricating oils and industrial greases. "
        "If these products are relevant to your current or upcoming sourcing needs, "
        "we would be glad to review your specifications and discuss possible options.\n\n"
        f"Best regards,\n{COMPANY['brand']}\n{website}"
    )


def send_email(to, content, attempts=1):
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



def make_whatsapp_message(row):
    prompt = f"""Write one concise first-contact WhatsApp message for a B2B trade prospect on behalf of {COMPANY['brand']}.
We work in international sourcing/trade in petroleum products, chemicals, petrochemicals, steel and renewable energy.
Use only facts supplied below; do not invent the recipient's role, business needs, prices, savings, inventory or relationship.
Be professional and personal, 2-4 short sentences, identify our company, mention only a product area supported by the lead, and ask whether they have a current/upcoming requirement. No email subject, no markdown, no emojis, no pressure.
Company/page: {row.get('company_name','')}
Country: {row.get('country','')}
Industry: {row.get('industry','')}
Product interest: {row.get('product_interest','')}
Public profile description: {row.get('social_bio','')}
Public evidence: {row.get('evidence','')}
Website: {row.get('website','')}
Return only the message text.
"""
    generated = llm(prompt)
    if generated:
        return generated
    company = (row.get("company_name") or "your team").strip()
    return (
        f"Hello {company} team, this is {COMPANY['brand']} reaching out about possible international sourcing cooperation. "
        "We work with steel, industrial chemicals and petrochemical products. "
        "If any of these are relevant to your current or upcoming needs, would you be open to reviewing specifications?"
    )


def send_whatsapp(raw_number, content, attempts=1):
    """Send outbound text through Wuapi only; never fetch chats or inbound messages."""
    if os.getenv("WHATSAPP_ENABLED", "false").lower() != "true":
        return "draft_only"

    api_key = os.getenv("WUAPI_API_KEY", "").strip()
    account_id = os.getenv("WUAPI_ACCOUNT_ID", "").strip()
    if not api_key or not account_id:
        print("WhatsApp skipped: WUAPI_API_KEY or WUAPI_ACCOUNT_ID is missing.")
        return "missing_wuapi_config"

    raw = (raw_number or "").strip()
    if not content or not content.strip():
        return "skipped_empty_message"

    # Wuapi accepts E.164 contact numbers and WhatsApp group IDs.
    if raw.endswith("@g.us"):
        recipient = raw
    else:
        if not (raw.startswith("+") or raw.startswith("00")):
            return "skipped_number_not_international"
        digits = re.sub(r"\D", "", raw)
        if raw.startswith("00"):
            digits = digits[2:]
        if not (8 <= len(digits) <= 15):
            return "skipped_invalid_number"
        recipient = "+" + digits

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    run_id = os.getenv("GITHUB_RUN_ID", "manual")
    # Stable within a workflow run: a retry cannot enqueue the same message twice.
    idempotency_key = f"rozhan-{run_id}-{re.sub(r'[^A-Za-z0-9@.+_-]', '', recipient)}"
    try:
        response = requests.post(
            "https://api.wuapi.dev/v1/messages",
            headers={**headers, "Idempotency-Key": idempotency_key[:200]},
            json={
                "accountId": account_id,
                "to": recipient,
                "text": content.strip(),
            },
            timeout=25,
        )
        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_code = str(error_data.get("code", "api_error")) if isinstance(error_data, dict) else "api_error"
            except ValueError:
                error_code = "api_error"
            print(f"WUAPI send failed: HTTP {response.status_code}; code={error_code}")
            return f"wuapi_http_{response.status_code}:{error_code}"

        try:
            data = response.json()
        except ValueError:
            data = {}
        status = str(data.get("status", "queued")).lower() if isinstance(data, dict) else "queued"
        message_id = str(data.get("id", "")) if isinstance(data, dict) else ""
        print(f"WUAPI_SEND_ACCEPTED recipient={recipient}; status={status}; message_id={message_id or 'not_returned'}")
        # Wuapi normally returns 202/queued. Track it as queued to prevent duplicate outreach.
        return "queued" if status == "queued" else ("sent" if status == "sent" else f"accepted:{status}")
    except requests.RequestException as exc:
        print(f"WUAPI send request failed: {type(exc).__name__}: {str(exc)[:180]}")
        return f"wuapi_request_failed:{type(exc).__name__}"


def quote_component(value):
    from urllib.parse import quote
    return quote(str(value), safe="")

def persist_outreach(rows, fieldnames):
    """Atomically checkpoint completed lead outcomes so a later timeout loses no progress."""
    OUTREACH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = OUTREACH.with_suffix(OUTREACH.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(OUTREACH)


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

    # Only successful delivery marks a lead as done. Failed AI/contact/transport
    # attempts remain eligible for a later retry when fresh public contact data appears.
    done = {
        key for row in existing
        if (row.get("status") == "sent" or row.get("whatsapp_status") in {"sent", "queued"})
        if (key := lead_key(row))
    }
    current_run_id = os.getenv("GITHUB_RUN_ID", "")
    # Only contact leads discovered by this run through the two explicitly
    # enabled social platforms. Do not drain historical web-search rows from
    # leads.csv: those rows can be stale, weakly qualified, or unrelated.
    candidates = []
    skipped_noncurrent = 0
    skipped_non_social = 0
    for candidate in rows:
        is_current = bool(current_run_id and candidate.get("run_id") == current_run_id)
        platform = (candidate.get("platform") or "").strip().lower()
        source = (candidate.get("source") or "").strip().lower()
        social_url = (candidate.get("social_url") or "").strip().lower()
        is_allowed_social = (
            platform in {"facebook", "linkedin"}
            and source in {"social:facebook", "social:linkedin"}
            and (
                ("facebook" in social_url and "linkedin" not in social_url)
                or ("linkedin.com" in social_url and "facebook" not in social_url)
            )
        )
        if not is_allowed_social:
            skipped_non_social += 1
            continue
        # Revisit only unresolved auto-parts social leads from earlier runs;
        # never revive old web-search rows or historical unrelated industries.
        industry = (candidate.get("industry") or "").strip().lower()
        if not is_current and industry != "automotive parts":
            skipped_noncurrent += 1
            continue
        key = lead_key(candidate)
        if key and key not in done:
            candidates.append(candidate)

    print(
        f"OUTREACH_QUEUE current_run_social_candidates={len(candidates)} "
        f"skipped_historical={skipped_noncurrent} skipped_non_social={skipped_non_social} "
        f"already_recorded={len(done)} limit={OUTREACH_LIMIT_PER_RUN}",
        flush=True,
    )

    fieldnames = [
        "company_name", "website", "email", "message", "status",
        "whatsapp_number", "whatsapp_message", "whatsapp_status", "run_id"
    ]
    # Preserve any historical columns so DictWriter never crashes on older CSV schemas.
    for old_row in existing:
        for field in old_row:
            if field not in fieldnames:
                fieldnames.append(field)
    new_rows = []

    for row in candidates[:OUTREACH_LIMIT_PER_RUN]:
        key = lead_key(row)
        if key and key in done:
            print(f"Outreach skipped: duplicate lead identity ({key[:120]})")
            continue
        company_label = (row.get("company_name") or row.get("website") or "unnamed lead").strip()
        print(f"OUTREACH_LEAD_START company={company_label[:100]}", flush=True)
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
            persist_outreach(existing + new_rows, fieldnames)
            print(f"OUTREACH_LEAD_DONE company={company_label[:100]} email_status=ai_failed whatsapp_status=not_attempted", flush=True)
            continue
        status = send_email(row.get("email", ""), message) if row.get("email") else "no_public_email"
        wa_number = (row.get("whatsapp") or row.get("phone") or "").strip()
        wa_message = make_whatsapp_message(row) if wa_number else ""
        if wa_number and wa_message:
            wa_status = send_whatsapp(wa_number, wa_message)
        elif wa_number:
            wa_status = "ai_failed"
        else:
            wa_status = "no_public_whatsapp_number"
        new_rows.append({
            "company_name": row.get("company_name", ""),
            "website": row.get("website", ""),
            "email": row.get("email", ""),
            "message": message,
            "status": status,
            "whatsapp_number": wa_number,
            "whatsapp_message": wa_message,
            "whatsapp_status": wa_status,
            "run_id": row.get("run_id", current_run_id),
        })
        if key:
            done.add(key)
        # Checkpoint each lead before beginning the next one; the workflow save
        # step can commit this file even if a later lead hits its step timeout.
        persist_outreach(existing + new_rows, fieldnames)
        print(
            f"OUTREACH_LEAD_DONE company={company_label[:100]} "
            f"email_status={status} whatsapp_status={wa_status}",
            flush=True,
        )

    all_rows = existing + new_rows
    persist_outreach(all_rows, fieldnames)

    sent_count = sum(1 for r in new_rows if r.get("status") == "sent")
    draft_count = sum(1 for r in new_rows if r.get("status") == "draft_only")
    failed_count = sum(1 for r in new_rows if r.get("status", "").startswith("send_failed"))
    no_email_count = sum(1 for r in new_rows if r.get("status") == "no_public_email")
    ai_failed_count = sum(1 for r in new_rows if r.get("status") == "ai_failed")
    wa_sent = sum(1 for r in new_rows if r.get("whatsapp_status") == "sent")
    wa_failed = sum(1 for r in new_rows if str(r.get("whatsapp_status", "")).startswith("send_failed"))
    wa_no_number = sum(1 for r in new_rows if r.get("whatsapp_status") == "no_public_whatsapp_number")
    print(
        f"Outreach summary: generated={len(new_rows)}; draft_only={draft_count}; "
        f"email_sent={sent_count}; email_send_failures={failed_count}; "
        f"no_public_email={no_email_count}; ai_failed={ai_failed_count}; "
        f"whatsapp_sent={wa_sent}; whatsapp_send_failures={wa_failed}; "
        f"no_public_whatsapp_number={wa_no_number}; "
        f"email_enabled={os.getenv('SEND_EMAILS', 'false').lower() == 'true'}; "
        f"whatsapp_enabled={os.getenv('WHATSAPP_ENABLED', 'false').lower() == 'true'}"
    )


if __name__ == "__main__":
    main()
