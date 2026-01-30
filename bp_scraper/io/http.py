from __future__ import annotations

import random
import time
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from requests.exceptions import RequestException, Timeout, ConnectionError as ReqConnectionError

from bp_scraper.core.constants import (
    SESSION,
    DEFAULT_BACKOFF,
    DEFAULT_DELAY,
    DEFAULT_JITTER,
    DEFAULT_RETRIES,
    BASE,
    CANON_SENATE_STATE_URL,
    CANON_HOUSE_DISTRICT_URL,
    CANON_HOUSE_STATE_URL,
    CANON_STATE_GOV_URL,
    CANON_STATE_AG_URL,
    CANON_STATE_LOWER_URL,
    CANON_STATE_UPPER_URL,
)

try:
    from bp_scraper.core.constants import CANON_STATE_LTGOV_URL  
except Exception:
    CANON_STATE_LTGOV_URL = None  

try:
    from bp_scraper.core.constants import CANON_STATE_LT_GOV_URL  
except Exception:
    CANON_STATE_LT_GOV_URL = None  

try:
    from bp_scraper.core.constants import CANON_STATE_LEG_DISTRICT_URL  
except Exception:
    CANON_STATE_LEG_DISTRICT_URL = None  


_BLOCKPAGE_HINTS = [
    "are you a human",
    "unusual traffic",
    "robot check",
    "attention required",
    "access denied",
    "enable cookies",
    "checking your browser",
]


def get_soup(
    url: str,
    delay: Optional[float] = None,
    retries: Optional[int] = None,
    backoff: float = DEFAULT_BACKOFF,
    jitter: float = DEFAULT_JITTER,
) -> BeautifulSoup:
    effective_delay = DEFAULT_DELAY if delay is None else float(delay)
    max_retries = DEFAULT_RETRIES if retries is None else int(retries)

    attempt_index = 0
    last_exception: Optional[Exception] = None

    while attempt_index <= max_retries:
    
        if attempt_index > 0:
            sleep_seconds = min(
                60.0,
                (effective_delay * (backoff ** attempt_index)) + random.uniform(0, jitter),
            )
            time.sleep(sleep_seconds)

        try:
            response = SESSION.get(url, timeout=45)
            status_code = response.status_code
            response_start_lower = response.text[:4000].lower()

            if status_code in (429, 403) or any(hint in response_start_lower for hint in _BLOCKPAGE_HINTS):
                last_exception = RuntimeError(f"Transient block (HTTP {status_code})")
                attempt_index += 1
                continue

            response.raise_for_status()

            time.sleep(effective_delay + random.uniform(0, jitter))
            return BeautifulSoup(response.text, "lxml")

        except (Timeout, ReqConnectionError, RequestException) as request_error:
            last_exception = request_error
            attempt_index += 1
            continue

    if last_exception:
        raise last_exception
    raise RuntimeError("Failed to fetch page after retries")


def _strip_query_frag(url_string: str) -> str:
    split_parts = urlsplit(url_string)
    return urlunsplit((split_parts.scheme, split_parts.netloc, split_parts.path, "", ""))


def canonicalize_url(url_string: str, year: int, chamber: str) -> Optional[str]:
    """
    Turn a raw href into a canonical, year-scoped Ballotpedia URL for the given chamber/scope.

    chamber:
      - "senate"
      - "house"
      - "state"  (odd-year state offices + legislature pages)
    """
    if not url_string:
        return None

    if not url_string.startswith("http"):
        if url_string.startswith("/"):
            url_string = BASE.rstrip("/") + url_string
        else:
            return None

    base_without_query = _strip_query_frag(url_string)
    lower_url = base_without_query.lower()

    if any(token in lower_url for token in ("/index.php?", "/amp", ":amp", "/m.", "printable", "mobileaction")):
        return None

    if not base_without_query.endswith(f"_{year}"):
        return None

    if chamber == "senate":
        return base_without_query if CANON_SENATE_STATE_URL.search(base_without_query) else None

    if chamber == "house":
        if CANON_HOUSE_STATE_URL.search(base_without_query) or CANON_HOUSE_DISTRICT_URL.search(base_without_query):
            return base_without_query
        return None

    if chamber == "state":
        if CANON_STATE_GOV_URL.search(base_without_query):
            return base_without_query
        if CANON_STATE_AG_URL.search(base_without_query):
            return base_without_query

        if (CANON_STATE_LT_GOV_URL is not None) and CANON_STATE_LT_GOV_URL.search(base_without_query):  
            return base_without_query
        if (CANON_STATE_LTGOV_URL is not None) and CANON_STATE_LTGOV_URL.search(base_without_query):  
            return base_without_query

        if CANON_STATE_LOWER_URL.search(base_without_query):
            return base_without_query
        if CANON_STATE_UPPER_URL.search(base_without_query):
            return base_without_query

        if (CANON_STATE_LEG_DISTRICT_URL is not None) and CANON_STATE_LEG_DISTRICT_URL.search(base_without_query):  # type: ignore
            return base_without_query

        return None

    return None
