"""Shared Gemini generation with bounded retries and multiple model fallbacks."""

import os
import random
import time

from google import genai
from google.genai import types

TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}
FATAL_CODES = {401, 403}


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
        or "504" in message
        or "429" in message
    )


def generate_text(prompt, *, api_key=None, model=None):
    """Generate text with bounded retries; try other models after model/service errors."""
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

    timeout_ms = max(5_000, min(int(os.getenv("GEMINI_TIMEOUT_MS", "15000")), 30_000))
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
    last_error = None

    for model_index, candidate in enumerate(models):
        # Retry primary once for transient capacity/timeouts. For 429, move on immediately:
        # it often represents a quota/rate limit that a short retry will not resolve.
        attempts = 2 if model_index == 0 else 1
        for attempt in range(1, attempts + 1):
            try:
                response = client.models.generate_content(model=candidate, contents=prompt)
                generated = (response.text or "").strip()
                if generated:
                    if model_index:
                        print(f"Gemini fallback model succeeded: {candidate}")
                    return generated
                last_error = RuntimeError("Gemini returned an empty response")
                print(f"Gemini model returned empty text: {candidate}; trying next model.")
                break
            except Exception as exc:
                last_error = exc
                code = _error_code(exc)
                if code in FATAL_CODES:
                    raise RuntimeError(
                        f"Gemini authentication/permission failed (HTTP {code}); check GEMINI_API_KEY and project access."
                    ) from exc

                transient = _is_transient(exc)
                print(
                    f"Gemini model failure: model={candidate}; attempt={attempt}/{attempts}; "
                    f"code={code}; transient={transient}; error={type(exc).__name__}"
                )
                if transient and attempt < attempts and code != 429:
                    delay = min(2 ** attempt, 8) + random.uniform(0, 0.5)
                    print(f"Retrying Gemini model {candidate} in {delay:.1f}s.")
                    time.sleep(delay)
                    continue
                # Do not stop at a single overloaded or unavailable model. Try the next
                # configured model, while keeping total attempts bounded by this list.
                break

    code = _error_code(last_error) if last_error else None
    if code == 429:
        reason = "rate limit/quota or capacity limit (HTTP 429)"
    elif code in {503, 504, 408, 500, 502}:
        reason = f"temporary Gemini service/timeout failure (HTTP {code})"
    else:
        reason = type(last_error).__name__ if last_error else "UnknownError"
    raise RuntimeError(
        f"Gemini generation failed after trying {len(models)} configured model(s): {reason}"
    ) from last_error
