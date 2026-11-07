"""client.py, offline: throttling, error mapping, request shapes and the two-step cite export.

The site is replaced by httpx.MockTransport, so nothing here touches the network."""
import asyncio
import json
import time
import types

import httpx
import pytest

from airiti_mcp import client
from airiti_mcp.errors import AiritiNetworkError, AiritiParseError


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch):
    """No real 1.5 s waits in these tests (the tests about the interval set their own)."""
    monkeypatch.setattr(client, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(client, "_last_request_at", 0.0)


def mock_site(monkeypatch, handler):
    """Make every httpx.AsyncClient the module creates talk to `handler` instead of the network."""
    real = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def ok(request):
    return httpx.Response(200, text="ok")


# ------------------------------------------------------------------ throttle
async def test_throttle_sleeps_only_the_missing_part_of_the_interval(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(round(seconds, 3))

    monkeypatch.setattr(client, "MIN_INTERVAL", 1.5)
    monkeypatch.setattr(client, "time", types.SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(client, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as c:
        monkeypatch.setattr(client, "_last_request_at", 99.0)  # the previous request was 1.0 s ago
        resp = await client._throttled(c, "GET", "https://example.test/")
        assert resp.text == "ok" and sleeps == [0.5] and client._last_request_at == 100.0
        monkeypatch.setattr(client, "_last_request_at", 90.0)  # long ago: no waiting at all
        await client._throttled(c, "GET", "https://example.test/")
        assert sleeps == [0.5]


async def test_concurrent_requests_are_serialised_and_spaced(monkeypatch):
    monkeypatch.setattr(client, "MIN_INTERVAL", 0.05)
    stamps = []

    def handler(request):
        stamps.append(time.monotonic())
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await asyncio.gather(*(client._throttled(c, "GET", "https://example.test/") for _ in range(4)))
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) == 4 and all(g >= 0.045 for g in gaps)  # four callers at once, still one request per interval


async def test_a_cancelled_request_releases_the_lock_and_still_counts_for_the_spacing(monkeypatch):
    started = asyncio.Event()

    async def slow(request):
        started.set()
        await asyncio.sleep(30)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as c:
        task = asyncio.create_task(client._throttled(c, "GET", "https://example.test/"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not client._lock.locked() and client._last_request_at > 0  # the next caller is neither blocked nor early
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="again"))) as c:
        assert (await client._throttled(c, "GET", "https://example.test/")).text == "again"


# ------------------------------------------------------------------ error mapping
@pytest.mark.parametrize(
    "handler, message",
    [
        (lambda r: httpx.Response(502), "HTTP 502"),
        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)), "逾時"),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("no route", request=r)), "無法連線華藝（ConnectError）"),
    ],
    ids=["non-200", "timeout", "connection"],
)
async def test_failures_become_a_network_error_the_model_can_read(handler, message):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(AiritiNetworkError, match=message):
            await client._throttled(c, "GET", "https://example.test/")


# ------------------------------------------------------------------ request shapes
async def test_search_request_carries_the_query_as_the_site_expects_it(monkeypatch):
    seen = {}

    def handler(request):
        seen["method"], seen["path"], seen["ua"] = request.method, request.url.path, request.headers["user-agent"]
        seen["q"] = json.loads(request.url.params["queryString"])
        return httpx.Response(200, text="<html>search</html>")

    mock_site(monkeypatch, handler)
    assert await client.fetch_search_html("鷹架 數學", 3, 20) == "<html>search</html>"
    assert seen["method"] == "POST" and seen["path"] == "/Article/Query"
    assert seen["q"]["DSF"]["Page"] == 3 and seen["q"]["DSF"]["PageSize"] == 20
    assert [w["SearchKeyWord"] for w in seen["q"]["BSF"]["SearchFiledList"]] == ["鷹架", "數學"]  # every word must match
    assert "metadata-and-abstract-only" in seen["ua"]  # we say who we are and what we fetch


async def test_detail_request_encodes_a_docid_with_a_plus_exactly_once(monkeypatch):
    seen = {}

    def handler(request):
        seen["method"], seen["raw"] = request.method, request.url.raw_path
        return httpx.Response(200, text="<html>detail</html>")

    mock_site(monkeypatch, handler)
    await client.fetch_detail_html("15601277-49-72+171")
    assert seen["method"] == "GET" and seen["raw"] == b"/Article/Detail?DocID=15601277-49-72%2B171"


async def test_cite_export_fetches_the_token_then_exports_with_it_and_the_cookie(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/Article/CiteWindow":
            return httpx.Response(
                200, text="var ajaxRequestVerificationToken_CiteWindow = 'TOK:123';", headers={"set-cookie": "sid=abc; Path=/"}
            )
        assert request.headers["AjaxRequestVerificationToken"] == "TOK:123"
        assert request.headers.get("cookie") == "sid=abc"  # both steps share one client, so the cookie comes back
        body = request.content.decode()
        # the id list is percent-encoded and then form-encoded again - exactly what the site's own page sends
        assert "type=RIS" in body and "jsString=%255B%2522DOC%252B1%2522%255D" in body
        return httpx.Response(200, text='{"ReturnValue":0,"ReturnData":{"ID":"x","Content":"ok"}}')

    mock_site(monkeypatch, handler)
    out = await client.fetch_cite_export(["DOC+1"], "RIS")
    assert json.loads(out)["ReturnValue"] == 0
    assert seen == [("POST", "/Article/CiteWindow"), ("POST", "/Article/CiteExport")]


async def test_cite_export_reports_a_changed_site_when_the_token_is_missing(monkeypatch):
    mock_site(monkeypatch, lambda r: httpx.Response(200, text="<html>no token here</html>"))
    with pytest.raises(AiritiParseError, match="防偽 token"):
        await client.fetch_cite_export(["DOC1"], "RIS")
