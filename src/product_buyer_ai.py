"""Dedicated, single-call AI that tells social collection which B2B companies to find.

This module is intentionally isolated from the existing outreach Gemini utility.
It uses Gemma 4 26B A4B only, makes one request per collection run, performs no
web search, and returns None on any failure so the collector can use its existing
product-specific deterministic fallback.
"""

import json
import os
import re

from google import genai
from google.genai import types


DEFAULT_MODEL = "gemma-4-26b-a4b-it"
MAX_COMPANY_TYPES = 8
MAX_SEARCH_KEYWORDS = 12


def _clean_list(value, limit):
    if not isinstance(value, list):
        return []
    cleaned = []
    seen = set()
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip(" \t\r\n,;")
        key = text.casefold()
        if text and key not in seen and len(text) <= 100:
            seen.add(key)
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def generate_product_buyer_profile(product, target_country):
    """Return buyer-company types and product search terms from one model call."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("BUYER_AI_UNAVAILABLE reason=GEMINI_API_KEY_missing; using_existing_rules=true")
        return None

    model = os.getenv("BUYER_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    prompt = f"""
You are a narrowly scoped B2B export buyer-type analyst for an international trading
company. Your ONLY task is to tell a social-media lead collector which TYPES OF
COMPANIES are plausible buyers or commercial customers for the given product.

PRODUCT: {product}
TARGET COUNTRY: {target_country}

Return only one valid JSON object with exactly these fields:
{{
  "company_types": ["..."],
  "search_keywords": ["..."]
}}

Rules:
- Give 4 to 8 specific company categories that can realistically BUY, IMPORT,
  DISTRIBUTE, PROCESS, BLEND, or CONSUME this exact product in the target market.
- Select roles according to the product's real supply chain; do not use generic
  categories if a more precise buyer category exists.
- Do not list individual job titles, people, websites, or named companies.
- Do not broaden into unrelated products or unrelated industries.
- Provide 6 to 12 short search phrases, mainly English plus useful local-language
  terms for the target country when applicable.
- Include product synonyms and the buyer-company category in phrases where useful.
- Do not perform web searches or claim that any company is verified.
- JSON only; no markdown fences or explanations.
"""

    stage = "client_init"
    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=20_000),
        )
        stage = "model_request"
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=700,
            ),
        )
        stage = "read_model_response"
        raw = (response.text or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model_response_not_json")
        stage = "parse_model_json"
        payload = json.loads(raw[start:end + 1])
        stage = "validate_buyer_terms"
        company_types = _clean_list(payload.get("company_types"), MAX_COMPANY_TYPES)
        search_keywords = _clean_list(payload.get("search_keywords"), MAX_SEARCH_KEYWORDS)
        if len(company_types) < 3 or len(search_keywords) < 4:
            raise ValueError("model_response_missing_required_buyer_terms")
        return {
            "company_types": company_types,
            "search_keywords": search_keywords,
            "model": model,
        }
    except Exception as exc:
        # One attempt only: do not spend extra quota on retries or model fallbacks.
        # Log a short, single-line diagnostic; never log the API key or raw response.
        error_message = " ".join(str(exc).split())[:240]
        if api_key:
            error_message = error_message.replace(api_key, "[REDACTED]")
        print(
            "BUYER_AI_UNAVAILABLE "
            f"model={model}; stage={stage}; error={type(exc).__name__}; "
            f"detail={error_message or 'no_error_detail'}; "
            "fallback=existing_product_rules"
        )
        return None
