"""Shared Gemini generation with bounded retries and transient-error model fallback."""

import os
import random
import time

from google import genai, types

TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


def _error_code(exc):
    for name in ("code", "status_code", "http_status"):
        value = getattr(exc, name, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _is_transient(exc):
    code = _error_code(exc)
    if code is not None:
        return code in TRANSIENT_CODES
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        "timeout" in name
        or "connection" in name
        or "temporarily unavailable" in message
        or "service unavailable" in message
        or "high demand" in message
        or "503" in message
        or "429" in message
    )


def generate_text(prompt, *, api_key=None, model=None):
    """Generate text; retry transient failures and only then try configured fallbacks."""
    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    primary = (model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")).strip()
    configured = os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite",
    )
    models = []
    for candidate in [primary, *[x.strip() for x in configured.split(",")]]:
        if candidate and candidate not in models:
            models.append(candidate)

    # Bound each network request so transient provider stalls cannot consume the whole workflow timeout.
    timeout_ms = max(5_000, min(int(os.getenv("GEMINI_TIMEOUT_MS", "15000")), 30_000))
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
    last_error = None

    # Keep one fallback only: bounded request time is more valuable than exhausting
    # every fallback model during a short scheduled automation run.
    models = models[:2]
    for model_index, candidate in enumerate(models):
        attempts = 2 if model_index == 0 else 1
        for attempt in range(1, attempts + 1):
            try:
                response = client.models.generate_content(model=candidate, contents=prompt)
                text = (response.text or "").strip()
                if text:
                    if model_index:
                        print(f"Gemini fallback model succeeded: {candidate}")
                    return text
                raise RuntimeError("Gemini returned an empty response")
            except Exception as exc:
                last_error = exc
                transient = _is_transient(exc)
                if not transient:
                    raise
                if attempt < attempts:
                    delay = min(2 ** attempt, 16) + random.uniform(0, 1)
                    print(
                        f"Gemini transient error on {candidate} "
                        f"(attempt {attempt}/{attempts}, code={_error_code(exc)}); "
                        f"retrying in {delay:.1f}s."
                    )
                    time.sleep(delay)
                else:
                    print(
                        f"Gemini model {candidate} exhausted {attempts} attempts "
                        f"after a transient error (code={_error_code(exc)})."
                    )
        # Only fall through to the next model after transient failures.

    raise RuntimeError(
        "Gemini generation failed after bounded retries and fallback models: "
        f"{type(last_error).__name__ if last_error else 'UnknownError'}"
    ) from last_error
