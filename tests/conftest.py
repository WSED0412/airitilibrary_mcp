import json

import pytest

from airiti_mcp import harvest, server
from airiti_mcp.errors import AiritiNetworkError
from airiti_mcp.parsers import SearchPage
from helpers import CRLF, _item


@pytest.fixture(autouse=True)
def _fresh_run_locks():
    """harvest keeps one asyncio.Lock per run folder in a module-level dict; every test gets a clean one."""
    harvest._locks.clear()
    yield
    harvest._locks.clear()


@pytest.fixture
def fake_site(monkeypatch, tmp_path):
    """total results served 20 per page; `fail_page` raises a network error; `overlap` repeats the
    last item of each page at the start of the next (the site can shift results between requests)."""
    monkeypatch.setenv("AIRITI_EXPORT_DIR", str(tmp_path))
    state = {"total": 45, "fail_page": None, "overlap": False, "bad_id": None, "export_calls": [],
             "step": 0.0, "export_step": 0.0, "pages": [], "empty": {}, "sleeps": [], "size": 20}
    clock = {"t": 0.0}  # fake time for the pause/resume tests: each request advances it by step / export_step
    monkeypatch.setattr(harvest, "_now", lambda: clock["t"])

    async def fake_sleep(seconds):  # retry back-off: no real waiting, but the fake clock moves
        state["sleeps"].append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(harvest, "_sleep", fake_sleep)

    async def fetch(query, page, page_size):
        state["pages"].append(page)
        state["size"] = page_size  # parse() runs right after fetch(): it serves exactly the page size that was asked for
        clock["t"] += state["step"]
        if state["fail_page"] == page:
            raise AiritiNetworkError("boom")
        if state["empty"].get(page, 0) > 0:  # the site's flaky "0 筆" answer for a valid page
            state["empty"][page] -= 1
            return "E"
        return str(page)

    def parse(html):
        if state["total"] == 0 or html == "E":
            return SearchPage(total=0)
        page = int(html)
        size = state["size"]
        start = (page - 1) * size - (1 if state["overlap"] and page > 1 else 0)
        if start >= state["total"]:  # out of range: the site gives the same "0 筆" block
            return SearchPage(total=0)
        end = min(page * size, state["total"])
        return SearchPage(total=state["total"], items=[_item(n, "學位論文" if n % 10 == 3 else "期刊") for n in range(start, end)])

    async def fake_export(ids, site_type):
        state["export_calls"].append(list(ids))
        clock["t"] += state["export_step"]
        if state["bad_id"] in ids:  # the real site fails the whole batch and does not say which id
            return json.dumps({"ReturnValue": 1, "ReturnMessage": "處理失敗", "ReturnData": None})
        def record(i):  # what the site sends depends on the format asked for, and theses (DocID U...) are a different type
            thesis = i.startswith("U")
            if site_type == "BibTeX":
                return "@" + ("phdthesis" if thesis else "article") + "{" + i + "," + CRLF + "title ={" + i + "}," + CRLF + "}" + CRLF + CRLF
            if site_type == "Endnote":
                return "%0 " + ("Thesis" if thesis else "Journal Article") + CRLF + "%T " + i + CRLF + CRLF
            return "TY  - " + ("THES" if thesis else "JOUR") + CRLF + "T1  - " + i + CRLF + "ER  - " + CRLF + CRLF

        content = "".join(record(i) for i in ids)
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    monkeypatch.setattr(server.client, "fetch_cite_export", fake_export)
    monkeypatch.setattr(server.client, "fetch_search_html", fetch)
    monkeypatch.setattr(server.parsers, "parse_search", parse)
    return state, tmp_path
