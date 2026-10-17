"""Conservative, explicitly verified context limits for the configured writer.

Custom model adapters never inherit a provider profile, even when their ambient
environment names the same service. Limits concern complete serialized requests,
not a heuristic token count or permission to truncate original material.
"""
import json
import os
from urllib.parse import urlsplit


PROVIDER_DOCUMENTATION = "https://api-docs.deepseek.com/quick_start/pricing/"
DOCUMENTATION_CHECKED_AT = "2026-10-16"
OFFICIAL_CONTEXT_CHARACTERS = 500000
OFFICIAL_REQUEST_UTF8_BYTES = 800000
LONG_SOURCE_OUTPUT_TOKENS = 32768


class ContextBudgetExceeded(ValueError):
    """The unchanged request exceeds the declared conservative local budget."""


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def official_profile(model=None):
    if model is not None or os.getenv("LLM_MODEL", "") != "deepseek-flash":
        return None
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    try:
        address = urlsplit(base)
        if (address.scheme != "https" or address.hostname != "api.deepseek.com"
                or address.username or address.password or address.query or address.fragment
                or address.port not in (None, 443)):
            return None
    except ValueError:
        return None
    return {"id": "official-deepseek-v4.1-flash-context-v1", "base_url": base, "model": "deepseek-flash",
            "documented_model": "DeepSeek-V4.1-Flash", "documented_context_tokens": 1000000,
            "documentation_url": PROVIDER_DOCUMENTATION, "documentation_checked_at": DOCUMENTATION_CHECKED_AT,
            "max_context_characters": OFFICIAL_CONTEXT_CHARACTERS, "max_request_utf8_bytes": OFFICIAL_REQUEST_UTF8_BYTES,
            "long_source_output_tokens": LONG_SOURCE_OUTPUT_TOKENS,
            "policy": "conservative_complete_request_budget_no_truncation"}


def context_character_limit(model=None, default=160000):
    profile = official_profile(model)
    return profile["max_context_characters"] if profile else default


def check_context(context, model=None, default_characters=160000):
    content = serialized(context)
    profile = official_profile(model)
    limit = profile["max_context_characters"] if profile else default_characters
    if len(content) > limit:
        raise ContextBudgetExceeded(f"Context has {len(content)} characters, exceeding its {limit}-character budget; no material was truncated")
    return {"characters": len(content), "utf8_bytes": len(content.encode("utf-8")),
            "character_limit": limit, "provider_profile": profile}


def check_messages(messages, model=None):
    content = serialized(messages)
    profile = official_profile(model)
    size = len(content.encode("utf-8"))
    limit = profile["max_request_utf8_bytes"] if profile else None
    if limit is not None and size > limit:
        raise ContextBudgetExceeded(f"Complete request has {size} UTF-8 bytes, exceeding its {limit}-byte budget; no material was truncated")
    return {"characters": len(content), "utf8_bytes": size, "byte_limit": limit, "provider_profile": profile}
