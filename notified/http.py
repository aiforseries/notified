"""Shared HTTP client (curl_cffi with a standard browser TLS profile, which many
.gov.in servers expect). Adapters must only read openly published listings; a site
that actively blocks automated clients is left disabled rather than worked around."""
from __future__ import annotations

import threading
import time
import warnings
from urllib.parse import urljoin

from curl_cffi import requests as _cr

warnings.filterwarnings("ignore")

DEFAULT_TIMEOUT = 40
_local = threading.local()


def session() -> _cr.Session:
    """One session per thread (sources are fetched in parallel)."""
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = _cr.Session(impersonate="chrome")
        s.headers.update({"Accept-Language": "en-IN,en;q=0.9"})
    return s


def get(url: str, *, params=None, headers=None, timeout: int = DEFAULT_TIMEOUT, retries: int = 2, verify: bool = False):
    """GET with retries. TLS verification is off by default: many .gov.in sites ship broken chains."""
    last = None
    for attempt in range(retries + 1):
        try:
            r = session().get(url, params=params, headers=headers, timeout=timeout, verify=verify, allow_redirects=True)
            if r.status_code >= 500 and attempt < retries:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def post(url: str, *, data=None, json=None, headers=None, timeout: int = DEFAULT_TIMEOUT, retries: int = 2, verify: bool = False):
    last = None
    for attempt in range(retries + 1):
        try:
            r = session().post(url, data=data, json=json, headers=headers, timeout=timeout, verify=verify)
            if r.status_code >= 500 and attempt < retries:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def text(r) -> str:
    """Decode a response body: UTF-8 first, then Windows-1252 (common on older .gov.in pages)."""
    raw = r.content
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def soup(url: str, **kw):
    from bs4 import BeautifulSoup
    r = get(url, **kw)
    r.raise_for_status()
    return BeautifulSoup(text(r), "lxml"), r


def absolute(base: str, href: str) -> str:
    return urljoin(base, (href or "").strip())
