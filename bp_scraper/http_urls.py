from __future__ import annotations
import random
import re
import time
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from requests.exceptions import RequestException, Timeout, ConnectionError as ReqConnectionError

from .constants import (
    SESSION, DEFAULT_BACKOFF, DEFAULT_DELAY, DEFAULT_JITTER, DEFAULT_RETRIES,
    BASE, CANON_SENATE_STATE_URL, CANON_HOUSE_DISTRICT_URL, CANON_HOUSE_STATE_URL,
)

_BLOCKPAGE_HINTS = [
    "are you a human", "unusual traffic", "robot check", "attention required",
    "access denied", "enable cookies", "checking your browser"
]

def get_soup(
    url: str,
    delay: Optional[float]=None,
    retries: Optional[int]=None,
    backoff: float=DEFAULT_BACKOFF,
    jitter: float=DEFAULT_JITTER
) -> BeautifulSoup:
    d = DEFAULT_DELAY if delay is None else float(delay)
    r = DEFAULT_RETRIES if retries is None else int(retries)

    attempt = 0
    last_exc: Optional[Exception] = None
    while attempt <= r:
        if attempt > 0:
            sleep_for = min(60.0, (d * (backoff ** attempt)) + random.uniform(0, jitter))
            time.sleep(sleep_for)
        try:
            resp = SESSION.get(url, timeout=45)
            status = resp.status_code
            text_low = resp.text[:4000].lower()

            if status in (429, 403) or any(h in text_low for h in _BLOCKPAGE_HINTS):
                last_exc = RuntimeError(f"Transient block (HTTP {status})")
                attempt += 1
                continue

            resp.raise_for_status()
            time.sleep(d + random.uniform(0, jitter))
            return BeautifulSoup(resp.text, "lxml")

        except (Timeout, ReqConnectionError, RequestException) as e:
            last_exc = e
            attempt += 1
            continue

    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to fetch page after retries")


def _strip_query_frag(u: str) -> str:
    p = urlsplit(u)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def canonicalize_url(u: str, year: int, chamber: str) -> Optional[str]:
    if not u: 
        return None
    if not u.startswith("http"):
        if u.startswith("/"): 
            u = BASE.rstrip("/") + u
        else: 
            return None
    base_no_q = _strip_query_frag(u)
    low = base_no_q.lower()

    if any(x in low for x in ("/index.php?","/amp",":amp","/m.","printable","mobileaction")):
        return None
    if not base_no_q.endswith(f"_{year}"):
        return None

    if chamber == "senate":
        return base_no_q if CANON_SENATE_STATE_URL.search(base_no_q) else None
    else:
        if CANON_HOUSE_STATE_URL.search(base_no_q) or CANON_HOUSE_DISTRICT_URL.search(base_no_q):
            return base_no_q
        return None
