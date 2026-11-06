"""airiti_search_all, offline: the site is replaced by a fake that serves 20 items per page."""
import csv
import io
import json

import pytest

from airiti_mcp import server
from mcp.server.mcpserver.exceptions import ToolError

from airiti_mcp.errors import AiritiNetworkError
from airiti_mcp.parsers import SearchItem, SearchPage
from airiti_mcp.search_export import build_csv, build_log, run_folder_name


CRLF = chr(13) + chr(10)


def _item(n: int, kind: str = "期刊") -> SearchItem:
    doc_id = f"U{n}" if kind == "學位論文" else f"DOC{n}"
    return SearchItem(doc_id, f"篇名{n}", [f"作者{n}", "乙"], "刊名/2024", kind, f"https://x/{n}", n % 2 == 0)


@pytest.fixture
def fake_site(monkeypatch, tmp_path):
    """total results served 20 per page; `fail_page` raises a network error; `overlap` repeats the
    last item of each page at the start of the next (the site can shift results between requests)."""
    monkeypatch.setenv("AIRITI_EXPORT_DIR", str(tmp_path))
    state = {"total": 45, "fail_page": None, "overlap": False, "bad_id": None, "export_calls": []}

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
        return SearchPage(total=state["total"], items=[_item(n, "學位論文" if n % 10 == 3 else "期刊") for n in range(start, end)])

    async def fake_export(ids, site_type):
        state["export_calls"].append(list(ids))
        if state["bad_id"] in ids:  # the real site fails the whole batch and does not say which id
            return json.dumps({"ReturnValue": 1, "ReturnMessage": "處理失敗", "ReturnData": None})
        content = "".join(f"TY  - JOUR{CRLF}T1  - {i}{CRLF}ER  - {CRLF}{CRLF}" for i in ids)
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    monkeypatch.setattr(server.client, "fetch_cite_export", fake_export)
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


async def test_search_all_exports_merged_file_and_skips_theses(fake_site):
    state, root = fake_site
    out = await server.airiti_search_all("x", export_format="ris")
    d = _only_run_dir(root)
    text = (d / "export.ris").read_bytes().decode("utf-8")
    assert text.count("TY  - JOUR") == 40 and "T1  - U3" not in text and "T1  - DOC0" in text  # 45 - 5 theses
    assert [len(c) for c in state["export_calls"]] == [20, 20]  # batches of 20
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "成功 40 篇" in log and "略過學位論文 5 篇" in log and "匯出失敗" not in log
    assert str(d / "export.ris") in out


async def test_search_all_isolates_invalid_doc_id_in_batch(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC7"
    await server.airiti_search_all("x", export_format="endnote")
    d = _only_run_dir(root)
    text = (d / "export.enw").read_text(encoding="utf-8")
    assert text.count("TY  - JOUR") == 39 and "T1  - DOC7" + CRLF not in text and "T1  - DOC6" in text
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "成功 39 篇" in log and "匯出失敗" in log and "DOC7" in log


async def test_search_all_without_export_format_does_not_export(fake_site):
    state, root = fake_site
    await server.airiti_search_all("x")
    assert state["export_calls"] == [] and not list(_only_run_dir(root).glob("export.*"))


async def test_search_all_rejects_bad_export_format_before_any_request(fake_site):
    state, root = fake_site
    with pytest.raises(ToolError):
        await server.airiti_search_all("x", export_format="pdf")
    assert list(root.iterdir()) == []
