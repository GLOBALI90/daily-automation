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

PILOT_LUBRICANT_TERMS = (
    "industrial lubricant", "industrial lubricants", "lubricating oil",
    "lubricating oils", "lubricating grease", "industrial grease",
    "hydraulic oil", "gear oil", "compressor oil", "turbine oil",
    "metalworking fluid", "润滑油", "工业润滑油", "润滑脂", "工业润滑脂",
)
PILOT_PRODUCER_TERMS = (
    "manufacturer", "manufacturing", "factory", "producer", "production",
    "blending plant", "工厂", "生产厂家", "制造厂", "制造商", "生产企业", "调和厂",
)
PILOT_CHINA_TERMS = (
    "china", "chinese", "jiangsu", "guangdong", "zhejiang", "shandong",
    "shanghai", "tianjin", "hebei", "liaoning", "fujian", "hubei",
    "suzhou", "nanjing", "wuxi", "changzhou", "nantong", "guangzhou",
    "shenzhen", "foshan", "dongguan", "huizhou", "ningbo", "hangzhou",
    "qingdao", "dongying", "yantai", "weifang", "jinan", "tangshan",
    "cangzhou", "dalian", "shenyang", "yingkou", "xiamen", "quanzhou",
    "fuzhou", "wuhan", "yichang",
)

def is_pilot_target(row):
    """Only allow evidenced China-based industrial-lubricant manufacturers."""
    website = (row.get("website") or "").strip().lower()
    evidence = " ".join(
        (row.get(field) or "").strip()
        for field in (
            "company_name", "website", "social_url", "social_bio", "evidence",
        )
    ).lower()
    has_lubricant = any(term in evidence for term in PILOT_LUBRICANT_TERMS)
    has_producer = any(term in evidence for term in PILOT_PRODUCER_TERMS)
    has_china = any(term in evidence for term in PILOT_CHINA_TERMS) or website.endswith(".cn")
    return (
        (row.get("country") or "").strip().lower() == "china"
        and has_lubricant and has_producer and has_china
    )

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
Business: international B2B sourcing and trade. For this lead, the selected product focus is {row.get('product_interest','')}.
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
- Focus on the selected product interest ({row.get('product_interest','')}) and the buyer's evidenced industry/use case. Do not introduce unrelated products.
- Do not introduce products outside the selected product focus or the lead's evidenced use case.
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
        f"Our current sourcing focus includes {row.get('product_interest','the relevant product category')}. "
        "If this product is relevant to your current or upcoming sourcing needs, "
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



def verified_whatsapp_number(row):
    """Return only a number explicitly classified as public WhatsApp contact.

    A generic public phone number is not proof that the number accepts WhatsApp
    messages, so never promote the phone field into an outbound WhatsApp target.
    """
    return (row.get("whatsapp") or "").strip()


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


def main_product_outreach():
    """Temporary observation mode: contact candidates with a usable public email or explicit WhatsApp contact."""
    product_file = ROOT / "data" / "product_leads.csv"
    if not product_file.exists():
        print("PRODUCT_OUTREACH skipped: product_leads.csv not found")
        return
    with product_file.open(encoding="utf-8", newline="") as handle:
        candidates = list(csv.DictReader(handle))
    existing = list(csv.DictReader(OUTREACH.open(encoding="utf-8"))) if OUTREACH.exists() else []
    fields = [
        "company_name", "website", "email", "message", "status",
        "whatsapp_number", "whatsapp_message", "whatsapp_status", "run_id",
        "product_interest", "ai_customer_fit", "ai_confidence", "source_url",
    ]
    for old in existing:
        for key in old:
            if key not in fields:
                fields.append(key)

    sent_emails = {
        (row.get("email") or "").strip().lower()
        for row in existing if row.get("status") == "sent" and row.get("email")
    }
    done_whatsapp = {
        re.sub(r"\D", "", row.get("whatsapp_number") or "")
        for row in existing
        if row.get("whatsapp_status") in {"sent", "queued"} and row.get("whatsapp_number")
    }

    queue = []
    for row in candidates:
        # Temporary observation mode: do not gate outreach on AI confidence,
        # AI review status, scrape status, or an email-domain match. Keep basic
        # contact-format checks and duplicate prevention while we observe results.
        email = (row.get("email") or "").strip().lower()
        email_match = re.fullmatch(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})", email, re.I)
        usable_email = bool(email_match and email not in sent_emails)

        # A generic phone is still not assumed to be WhatsApp; only use a WhatsApp
        # contact explicitly extracted from a public WhatsApp label/link.
        wa_number = verified_whatsapp_number(row)
        wa_digits = re.sub(r"\D", "", wa_number)
        usable_wa = bool(
            wa_number.startswith(("+", "00"))
            and 8 <= len(wa_digits) <= 15
            and wa_digits not in done_whatsapp
        )
        if usable_email or usable_wa:
            queue.append((row, usable_email, wa_number if usable_wa else ""))

    print(
        f"PRODUCT_OUTREACH_QUEUE eligible_companies={len(queue)} "
        f"sent_emails_before={len(sent_emails)} sent_or_queued_whatsapp_before={len(done_whatsapp)} "
        "temporary_gates=usable_public_email_or_explicit_whatsapp+dedupe"
    )
    for row, verified_email, wa_number in queue[:1]:
        company = (row.get("company_name") or row.get("website") or "unnamed lead").strip()
        message = make_message(row) if verified_email else ""
        email_status = "not_attempted_no_verified_official_email"
        if verified_email:
            if not message:
                email_status = "ai_failed"
            else:
                email_status = send_email(row.get("email", ""), message)

        wa_message = make_whatsapp_message(row) if wa_number else ""
        wa_status = "not_attempted_no_verified_whatsapp"
        if wa_number:
            wa_status = send_whatsapp(wa_number, wa_message) if wa_message else "ai_failed"

        outcome = {
            "company_name": row.get("company_name", ""),
            "website": row.get("website", ""),
            "email": row.get("email", "") if verified_email else "",
            "message": message,
            "status": email_status,
            "whatsapp_number": wa_number,
            "whatsapp_message": wa_message,
            "whatsapp_status": wa_status,
            "run_id": os.getenv("GITHUB_RUN_ID", row.get("run_id", "")),
            "product_interest": row.get("product_interest", ""),
            "ai_customer_fit": row.get("ai_customer_fit", ""),
            "ai_confidence": row.get("ai_confidence", ""),
            "source_url": row.get("source_url", ""),
        }
        existing.append(outcome)
        persist_outreach(existing, fields)
        if email_status == "sent":
            sent_emails.add((row.get("email") or "").strip().lower())
        if wa_status in {"sent", "queued"}:
            done_whatsapp.add(re.sub(r"\D", "", wa_number))
        print(
            f"PRODUCT_OUTREACH_RESULT company={company[:100]} "
            f"email_status={email_status} whatsapp_status={wa_status}"
        )
    if not queue:
        print("PRODUCT_OUTREACH: no candidate currently has a usable, non-duplicate public email or explicit WhatsApp contact")

def main():
    if os.getenv("OUTREACH_SOURCE", "social").strip().lower() == "product_web":
        main_product_outreach()
        return
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
        # Keep the current-run boundary and supported social-platform checks,
        # but temporarily disable the old China/industrial-lubricants pilot gate.
        if not is_current:
            skipped_noncurrent += 1
            continue
        key = lead_key(candidate)
        if key and key not in done:
            candidates.append(candidate)

    print(
        f"OUTREACH_QUEUE current_run_social_candidates={len(candidates)} "
        f"skipped_historical={skipped_noncurrent} skipped_non_social={skipped_non_social} "
        f"pilot_country_product_filter=disabled "
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
        wa_number = verified_whatsapp_number(row)
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
