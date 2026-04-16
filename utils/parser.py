import re
import logging

from core.cache import cache, cache_lock
from utils.http import _http_session

logger = logging.getLogger(__name__)

_URL_PATTERNS = [
    r'[?&]productIds=(\d+)',
    r'[?&]productId=(\d+)',
    r'/item/(\d+)\.(?:html|htm)',
    r'/item/(\d+)(?:\?|$)',
    r'/product/(\d+)',
    r'/i/(\d+)',
    r'/p/[^/]+/index\.html[?&]productIds=(\d+)',
    r'/ssr/.*?[?&]productIds=(\d+)',
    r'/[a-z0-9]+\.html\?.*?productId(?:s)?=(\d+)',
]


def _match_product_id_from_url(url):
    for pattern in _URL_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_product_id(text):
    cache_key = f"pid_{text}"

    try:
        cached = cache.get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    if not any(domain in text for domain in ['aliexpress.com', 'alix.live', 's.click.aliexpress.com']):
        return None

    direct_match = _match_product_id_from_url(text)
    if direct_match:
        with cache_lock:
            cache[cache_key] = direct_match
        return direct_match

    try:
        response = _http_session.head(text, allow_redirects=True, timeout=8)
        final_url = response.url
    except Exception:
        final_url = text

    result = _match_product_id_from_url(final_url)
    if result:
        with cache_lock:
            cache[cache_key] = result
    return result
