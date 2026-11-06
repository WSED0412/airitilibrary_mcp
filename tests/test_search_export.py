"""airiti_search_all, offline: the site is replaced by a fake that serves 20 items per page."""
import csv
import io

import pytest

from airiti_mcp import server
from mcp.server.mcpserver.exceptions import ToolError

from airiti_mcp.errors import AiritiNetworkError
from airiti_mcp.parsers import SearchItem, SearchPage
from airiti_mcp.search_export import build_csv, build_log, run_folder_name


def _item(n: int, kind: str = "期刊") -> SearchItem:
    return SearchItem(f"DOC{n}", f"篇名{n}", [f"作者{n}", "乙"], "刊名/2024", kind, f"https://x/{n}", n % 2 == 0)


@pytest.fixture
def fake_site(monkeypatch, tmp_path):
    """total results served 20 per page; `fail_page` raises a network error; `overlap` repeats the
    last item of each page at the start of the next (the site can shift results between requests)."""
    monkeypatch.setenv("AIRITI_EXPORT_DIR", str(tmp_path))
    state = {"total": 45, "fail_page": None, "overlap": False}

    async def fetch(query, page, page_size):
        if state["fail_page"] == page:
            raise AiritiNetworkError("boom")
        return str(page)

    def parse(html):
        if state["total"] == 0:
            return SearchPage(total=0)
        page = int(html)
        start = (page - 1) * 20 - (1 if state["overlap"] and page > 1 else 0)
        end = min(page * 20, state["total"])
        return SearchPage(total=state["total"], items=[_item(n) for n in range(start, end)])

    monkeypatch.setattr(server.client, "fetch_search_html", fetch)
    monkeypatch.setattr(server.parsers, "parse_search", parse)
    return state, tmp_path


def _rows(d):
    return list(csv.reader(io.StringIO((d / "results.csv").read_bytes().decode("utf-8-sig"), newline="")))


def _only_run_dir(root):
    dirs = [d for d in root.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    return dirs[0]


async def test_search_all_writes_csv_and_log(fake_site):
    state, root = fake_site
    out = await server.airiti_search_all("鷹架 數學")
    d = _only_run_dir(root)
    raw = (d / "results.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM so Excel opens it as UTF-8
    rows = _rows(d)
    assert len(rows) == 1 + 45 and rows[1][1] == "DOC0" and rows[1][3] == "作者0；乙"
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "鷹架 數學" in log and "華藝回報總筆數：45" in log and "共抓 3 頁" in log and "未觸及上限" in log
    assert "不完整" not in log and "截斷" not in log
    assert str(d / "results.csv") in out and "共 45 筆" in out


async def test_search_all_truncates_at_max_total(fake_site):
    state, root = fake_site
    out = await server.airiti_search_all("x", max_total=25)
    d = _only_run_dir(root)
    rows = _rows(d)
    assert len(rows) == 1 + 25  # second page fetched, then cut to the cap
    assert "結果被截斷" in (d / "search_log.txt").read_text(encoding="utf-8")
    assert "【截斷】" in out


async def test_search_all_keeps_partial_results_on_failure(fake_site):
    state, root = fake_site
    state["fail_page"] = 2
    out = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    rows = _rows(d)
    assert len(rows) == 1 + 20
    assert "【不完整】" in (d / "search_log.txt").read_text(encoding="utf-8") and "【不完整】" in out


async def test_search_all_dedupes_repeated_doc_ids(fake_site):
    state, root = fake_site
    state["overlap"] = True
    await server.airiti_search_all("x")
    d = _only_run_dir(root)
    ids = [r[1] for r in _rows(d)[1:]]
    assert len(ids) == len(set(ids)) == 45
    assert "去除重複 DocID 後 45 筆" in (d / "search_log.txt").read_text(encoding="utf-8")


async def test_search_all_zero_results_writes_nothing(fake_site):
    state, root = fake_site
    state["total"] = 0
    out = await server.airiti_search_all("沒有這個詞")
    assert "0 筆" in out and list(root.iterdir()) == []


async def test_search_all_rejects_bad_arguments(fake_site):
    with pytest.raises(ToolError):
        await server.airiti_search_all("  ")
    for bad in (0, 2001):
        with pytest.raises(ToolError):
            await server.airiti_search_all("x", max_total=bad)


def test_log_counts_kinds_and_flags_truncation():
    from datetime import datetime

    items = [_item(1), _item(2, "學位論文"), _item(3)]
    log = build_log(
        query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=500, fetched=3, unique=3,
        pages_fetched=1, page_size=20, cap=3, items=items,
    )
    assert "期刊 2" in log and "學位論文 1" in log and "結果被截斷" in log
    assert build_csv([]).startswith("序號,DocID")


def test_run_folder_name_is_filesystem_safe():
    from datetime import datetime

    name = run_folder_name('鷹架: 數學/解題?*"', datetime(2026, 11, 6, 15, 0, 0))
    assert name == "20261106_150000_鷹架_數學_解題"
    assert run_folder_name("???", datetime(2026, 11, 6, 15, 0, 0)).endswith("_query")
