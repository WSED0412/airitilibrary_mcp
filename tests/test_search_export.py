"""airiti_search_all, offline: the site is replaced by a fake that serves 20 items per page."""
import csv
import io
import json

import pytest

from airiti_mcp import harvest, server
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
    state = {"total": 45, "fail_page": None, "overlap": False, "bad_id": None, "export_calls": [],
             "step": 0.0, "export_step": 0.0, "pages": []}
    clock = {"t": 0.0}  # fake time for the pause/resume tests: each request advances it by step / export_step
    monkeypatch.setattr(harvest, "_now", lambda: clock["t"])

    async def fetch(query, page, page_size):
        state["pages"].append(page)
        clock["t"] += state["step"]
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
        clock["t"] += state["export_step"]
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
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "結果被截斷" in log and "【截斷】" in out
    # the 15 rows cut by the cap are NOT duplicates: the log must say so (PRISMA numbers)
    assert "移除 0 筆，剩 40 筆" in log and "超過上限而未納入：15 筆" in log and "納入清單（results.csv）：25 筆" in log


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
    assert "移除 2 筆，剩 45 筆" in (d / "search_log.txt").read_text(encoding="utf-8")


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


async def test_midrun_network_failure_separates_unattempted_from_failed(fake_site, monkeypatch):
    state, root = fake_site
    calls = []

    async def flaky(ids, site_type):
        calls.append(len(ids))
        if len(calls) == 2:  # second batch: network error
            raise AiritiNetworkError("boom")
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": "TY  - JOUR" + CRLF + "ER  - " + CRLF}})

    monkeypatch.setattr(server.client, "fetch_cite_export", flaky)
    out = await server.airiti_search_all("x", export_format="ris")
    log = (_only_run_dir(root) / "search_log.txt").read_text(encoding="utf-8")
    assert "匯出中斷" in log and "因中斷未處理】20 篇" in log
    assert "匯出失敗" not in log and "DOC22" not in log  # untried ids are not listed as invalid
    assert "成功 20 篇" in log and "因中斷未處理" in out


async def test_isolating_a_bad_id_costs_few_requests(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC7"
    await server.airiti_search_all("x", export_format="ris")
    # batch 1: 2 tries of the whole batch + 20 single tries (one each); batch 2: 1 call
    assert len(state["export_calls"]) == 2 + 20 + 1


async def test_lone_bad_id_is_not_retried_again_singly(fake_site):
    state, root = fake_site
    state["total"] = 1
    state["bad_id"] = "DOC0"
    await server.airiti_search_all("x", export_format="ris")
    assert len(state["export_calls"]) == 2  # the two batch attempts only
    assert "匯出失敗】1 篇" in (_only_run_dir(root) / "search_log.txt").read_text(encoding="utf-8")


async def test_export_citation_tool_validation_and_output(fake_site):
    state, root = fake_site
    for bad in ([], [""], [f"D{i}" for i in range(21)]):
        with pytest.raises(ToolError):
            await server.airiti_export_citation(bad, "ris")
    with pytest.raises(ToolError, match="不支援"):
        await server.airiti_export_citation(["DOC1"], "pdf")
    out = await server.airiti_export_citation(["DOC1", "DOC2", "DOC1"], "ris")  # duplicate dropped
    assert "共 2 篇" in out and out.count("TY  - JOUR") == 2 and state["export_calls"] == [["DOC1", "DOC2"]]
    one = await server.airiti_export_citation("DOC9", "bibtex")  # a bare string is accepted
    assert "共 1 篇" in one and state["export_calls"][-1] == ["DOC9"]


async def test_export_citation_tool_reports_site_message_on_failure(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC1"
    with pytest.raises(ToolError, match="處理失敗"):
        await server.airiti_export_citation(["DOC1", "DOC2"], "ris")
    assert len(state["export_calls"]) == 2  # one retry, then give up


def test_export_dir_env_tolerates_quotes_and_tilde(tmp_path, monkeypatch):
    from airiti_mcp.search_export import export_root

    target = tmp_path / "My Exports"
    monkeypatch.setenv("AIRITI_EXPORT_DIR", '"' + str(target) + '"')
    assert export_root() == target
    export_root().mkdir(parents=True)  # no WinError 123
    monkeypatch.setenv("AIRITI_EXPORT_DIR", "")
    assert export_root().name == "airiti_exports"


def _run_dirs(root):
    return [d for d in root.iterdir() if d.is_dir()]


async def test_pauses_in_harvest_then_resumes_without_refetching(fake_site):
    state, root = fake_site
    state["step"] = 50.0  # every page "takes" 50 s; budget is 10 s
    first = await server.airiti_search_all("x", time_budget_s=10)
    d = _only_run_dir(root)
    assert "已暫停" in first and f'resume="{d.name}"' in first and "抓取結果頁 2/3" in first
    assert (d / "state.json").exists() and (d / "results.csv").exists()
    assert not (d / "search_log.txt").exists()  # the log is written when the run is complete
    assert len(_rows(d)) == 1 + 40  # provisional list so far

    second = await server.airiti_search_all(resume=d.name, time_budget_s=10)
    assert "已暫停" not in second and "共 45 筆" in second
    assert state["pages"] == [1, 2, 3]  # nothing fetched twice
    assert len(_rows(d)) == 1 + 45
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "分 2 次呼叫完成" in log

    third = await server.airiti_search_all(resume=d.name)  # finished run: just shows the result again
    assert "共 45 筆" in third and state["pages"] == [1, 2, 3]


async def test_pauses_in_export_then_resumes_without_duplicates(fake_site):
    state, root = fake_site
    state["export_step"] = 50.0
    first = await server.airiti_search_all("x", export_format="ris", time_budget_s=10)
    d = _only_run_dir(root)
    assert "已暫停" in first and "匯出書目 20/40" in first
    assert (d / "export.ris").read_bytes().decode("utf-8").count("TY  - JOUR") == 20

    second = await server.airiti_search_all(resume=d.name, time_budget_s=10)
    assert "已暫停" not in second
    text = (d / "export.ris").read_bytes().decode("utf-8")
    titles = [l for l in text.split(CRLF) if l.startswith("T1  - ")]
    assert len(titles) == 40 == len(set(titles))  # every exportable result exactly once
    assert "成功 40 篇" in (d / "search_log.txt").read_text(encoding="utf-8")
    assert [len(c) for c in state["export_calls"]] == [20, 20]


async def test_resume_rejects_paths_and_unknown_runs(fake_site):
    state, root = fake_site
    for bad in ("../x", "a/b", "a" + chr(92) + "b", "C:x", "..", "no_such_run"):
        with pytest.raises(ToolError):
            await server.airiti_search_all(resume=bad)
    for bad_budget in (5, 901):
        with pytest.raises(ToolError, match="time_budget_s"):
            await server.airiti_search_all("x", time_budget_s=bad_budget)
    assert state["pages"] == []  # all rejected before any request


async def test_engine_reports_monotonic_progress(fake_site, tmp_path):
    state, root = fake_site
    st, _ = await harvest.start_run("x", 1000, "ris")
    folder = tmp_path / "run"
    folder.mkdir()
    seen = []

    async def progress(done, total, message):
        seen.append((done, total, message))

    assert await harvest.advance(st, folder, 90, progress) is True
    values = [d for d, _, _ in seen]
    assert values == sorted(values) and len(set(values)) == len(values)  # strictly increasing
    assert seen[-1][:2] == (6.0, 6) and all(t == 6 for _, t, _ in seen)  # 3 pages + 3 batch-units
    assert any("已抓" in m for _, _, m in seen) and any("已匯出" in m for _, _, m in seen)


async def test_over_a_real_mcp_session_ctx_is_hidden_and_progress_arrives(fake_site):
    from mcp.client import Client

    state, root = fake_site
    events = []

    async def on_progress(progress, total, message):
        events.append((progress, total, message))

    async with Client(server.mcp) as c:
        tool = next(t for t in (await c.list_tools()).tools if t.name == "airiti_search_all")
        props = tool.input_schema["properties"] if hasattr(tool, "input_schema") else tool.inputSchema["properties"]
        assert "ctx" not in props and {"resume", "time_budget_s", "export_format"} <= set(props)
        result = await c.call_tool("airiti_search_all", {"query": "x", "export_format": "ris"}, progress_callback=on_progress)
    assert "共 45 筆" in str(result)
    values = [e[0] for e in events]
    assert values and values == sorted(values) and len(set(values)) == len(values)
    assert events[-1][0] == events[-1][1] == 6 and any("已抓" in e[2] for e in events)
