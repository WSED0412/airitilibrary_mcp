"""HTTP layer: one place for rate limiting, timeout, headers and error mapping.

No caching of any kind (pages are marked noarchive) -- every call hits the site.
Only the article-search page, the article-detail page and the citation-export endpoints
(CiteWindow / CiteExport, used only by airiti_export_citation) are ever requested;
nothing related to full-text download is touched.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from urllib.parse import quote

import httpx

from .cite_export import parse_cite_token
from .errors import AiritiNetworkError

BASE = "https://www.airitilibrary.com"
TIMEOUT = 15.0
MIN_INTERVAL = 1.5  # seconds between any two requests to the site
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; airiti-mcp/0.1; metadata-and-abstract-only)",
    "Accept-Language": "zh-TW",
}

_lock = asyncio.Lock()
_last_request_at = 0.0


async def _throttled(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    global _last_request_at
    async with _lock:  # serialises requests so the interval holds under concurrency
        wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise AiritiNetworkError(f"連線華藝逾時（{TIMEOUT:.0f} 秒）：{url}") from e
        except httpx.HTTPError as e:
            raise AiritiNetworkError(f"無法連線華藝（{type(e).__name__}）：{e}") from e
        finally:
            _last_request_at = time.monotonic()
    if resp.status_code != 200:
        raise AiritiNetworkError(f"華藝回應 HTTP {resp.status_code}：{url}")
    return resp


def detail_url(doc_id: str) -> str:
    return f"{BASE}/Article/Detail?DocID={quote(doc_id, safe='')}"


def build_query_string(query: str, page: int, page_size: int) -> str:
    """JSON shape verified in recon. Key spellings (SearchFileds / SearchFiledList)
    are the site's own and must not be 'fixed'. FieldName 49 = all fields incl. full text
    (DSF); BSF is the whitespace-split keyword list and is required for results."""
    words = query.split()
    obj = {
        "查詢時間": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        "查詢歷史類型代碼": "ADLang",
        "是否關鍵字統計": True,
        "DSF": {
            "SearchFileds": [{"FieldName": 49, "SearchKeyWord": " ".join(words), "FieldQuery": True}],
            "IsFuzzySearch": False,
            "PageSize": page_size,
            "Page": page,
        },
        "BSF": {
            "SearchFiledList": [
                {"FieldName": 0, "SearchKeyWord": w, "FieldQuery": True, "FieldLogic": 0} for w in words
            ]
        },
    }
    return quote(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


async def fetch_search_html(query: str, page: int, page_size: int) -> str:
    url = f"{BASE}/Article/Query?queryString={build_query_string(query, page, page_size)}"
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as c:
        return (await _throttled(c, "POST", url)).text


async def fetch_cite_export(doc_ids: list[str], site_type: str) -> str:
    """Airiti's own export (BibTeX/RIS/Endnote) for one or more articles in a single file.
    Two requests: the cite window (gives the anti-forgery token and sets its cookie), then the
    export. Returns the raw JSON body."""
    js = quote(json.dumps(doc_ids, ensure_ascii=False, separators=(",", ":")), safe="")
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as c:
        window = await _throttled(c, "POST", f"{BASE}/Article/CiteWindow", data={"jsString": js})
        token = parse_cite_token(window.text)
        resp = await _throttled(
            c, "POST", f"{BASE}/Article/CiteExport",
            data={"jsString": js, "type": site_type}, headers={"AjaxRequestVerificationToken": token},
        )
        return resp.text


async def fetch_detail_html(doc_id: str) -> str:
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as c:
        return (await _throttled(c, "GET", detail_url(doc_id))).text
