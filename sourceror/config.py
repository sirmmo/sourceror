"""Runtime configuration, from environment with a UI-editable override file.

Only the LLM settings live here — everything else Sourceror does works without
configuration, and the LLM is strictly an optional enhancement.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"

DEFAULTS = {
    # Any OpenAI-compatible endpoint: OpenRouter, OpenAI, a local llama.cpp or
    # vLLM server, LiteLLM, etc.
    "llm_base_url": "https://openrouter.ai/api/v1",
    "llm_model": "openai/gpt-4o-mini",
    "llm_api_key": "",
    "llm_enabled": True,
    "llm_timeout": 45,
    # Generous by default: reasoning models spend most of this budget
    # on hidden reasoning before emitting any answer.
    "llm_max_tokens": 3000,
    "llm_temperature": 0.1,
}

ENV_MAP = {
    "llm_base_url": ("LLM_BASE_URL", "OPENAI_BASE_URL", "OPENROUTER_BASE_URL"),
    "llm_model": ("LLM_MODEL", "OPENROUTER_MODEL", "OPENAI_MODEL"),
    "llm_api_key": ("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"),
}

_lock = threading.Lock()
_cache: dict | None = None


def _from_env() -> dict:
    out = {}
    for key, names in ENV_MAP.items():
        for n in names:
            if os.environ.get(n):
                out[key] = os.environ[n]
                break
    return out


def load() -> dict:
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        cfg = dict(DEFAULTS)
        cfg.update(_from_env())
        if CONFIG_PATH.exists():
            try:
                # The saved file wins: it is what the user set in the UI.
                cfg.update({k: v for k, v in
                            json.loads(CONFIG_PATH.read_text()).items()
                            if k in DEFAULTS})
            except (json.JSONDecodeError, OSError):
                pass
        _cache = cfg
        return dict(cfg)


def save(updates: dict) -> dict:
    global _cache
    with _lock:
        cfg = dict(_cache) if _cache is not None else dict(DEFAULTS)
        for k, v in updates.items():
            if k in DEFAULTS:
                cfg[k] = v
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        try:
            CONFIG_PATH.chmod(0o600)      # it holds an API key
        except OSError:
            pass
        _cache = cfg
        return dict(cfg)


def public() -> dict:
    """Config safe to hand to the browser — the key is masked."""
    cfg = load()
    key = cfg.get("llm_api_key") or ""
    return {**cfg,
            "llm_api_key": f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("set" if key else ""),
            "llm_configured": bool(key)}
