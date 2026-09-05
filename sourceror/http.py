"""Shared HTTP plumbing: a polite session, per-host rate limiting, TTL cache."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from urllib.parse import urlsplit

import requests

# Wikimedia's API etiquette requires a descriptive User-Agent with contact info.
USER_AGENT = ("Sourceror/0.1 (Wikipedia source-checking helper; "
              "https://github.com/sirmmo/sourceror) python-requests")

# Minimum seconds between requests to the same host.
RATE_LIMITS = {
    "api.crossref.org": 0.12,
    "api.openalex.org": 0.12,
    "api.semanticscholar.org": 1.2,
    "archive.org": 0.5,
    "openlibrary.org": 0.3,
    "_default": 0.05,
}

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT,
                         "Accept-Encoding": "gzip, deflate"})
_adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32,
                                         max_retries=0)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

_last_hit: dict[str, float] = defaultdict(float)
_host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()

_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 3600


def _throttle(host: str) -> None:
    with _locks_guard:
        lock = _host_locks[host]
    delay = RATE_LIMITS.get(host, RATE_LIMITS["_default"])
    with lock:
        wait = _last_hit[host] + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def request(method: str, url: str, *, timeout: float = 15, cache: bool = True,
            **kw) -> requests.Response | None:
    key = (method, url, repr(sorted(kw.get("params", {}).items())) if kw.get("params") else "")
    if cache and method == "GET":
        with _cache_lock:
            hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
    host = urlsplit(url).netloc.lower()
    _throttle(host)
    try:
        resp = _session.request(method, url, timeout=timeout, **kw)
    except requests.RequestException:
        return None
    if cache and method == "GET" and resp.status_code < 500:
        with _cache_lock:
            _cache[key] = (time.monotonic(), resp)
    return resp


def get_json(url: str, **kw) -> dict:
    r = request("GET", url, **kw)
    if r is None or r.status_code >= 400:
        raise RuntimeError(f"GET {url} -> {r.status_code if r else 'network error'}")
    return r.json()


def try_json(url: str, **kw) -> dict | None:
    """Like get_json but returns None instead of raising."""
    r = request("GET", url, **kw)
    if r is None or r.status_code >= 400:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def get_text(url: str, **kw) -> str:
    r = request("GET", url, **kw)
    if r is None or r.status_code >= 400:
        raise RuntimeError(f"GET {url} -> {r.status_code if r else 'network error'}")
    return r.text


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
