"""airiti_search_all, offline: the site is replaced by a fake that serves 20 items per page."""
import csv
import io
import json

import pytest

from airiti_mcp import harvest, server
from mcp.server.mcpserver.exceptions import ToolError

from airiti_mcp.errors import AiritiError, AiritiNetworkError
from airiti_mcp.parsers import SearchItem, SearchPage
from airiti_mcp.search_export import build_csv, build_log, duplicate_groups, is_preprint, run_folder_name


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


async def test_persistent_failure_pauses_twice_then_gives_up_keeping_partial_results(fake_site):
    state, root = fake_site
    state["fail_page"] = 2
    first = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    assert "因錯誤暫停" in first and "連續第 1/3 次" in first and (d / "state.json").exists()
    assert not (d / "search_log.txt").exists()
    second = await server.airiti_search_all(resume=d.name)
    assert "連續第 2/3 次" in second
    third = await server.airiti_search_all(resume=d.name)  # third strike: give up, keep what we have
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "【不完整】" in third and "連續失敗 3 次後放棄" in log and len(_rows(d)) == 1 + 20
    assert state["pages"] == [1] + [2] * 9  # the same page, 3 tries per call over 3 calls - never skipped


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


async def test_search_all_exports_merged_file_and_includes_theses(fake_site):
    state, root = fake_site
    out = await server.airiti_search_all("x", export_format="ris")
    d = _only_run_dir(root)
    text = (d / "export.ris").read_bytes().decode("utf-8")
    assert text.count("TY  - JOUR") == 40 and text.count("TY  - THES") == 5 and "T1  - U3" in text and "T1  - DOC0" in text  # theses too
    assert [len(c) for c in state["export_calls"]] == [20, 20, 5]  # batches of 20
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "成功 45 篇" in log and "其中學位論文 5 篇" in log and "略過" not in log and "匯出失敗" not in log
    assert str(d / "export.ris") in out


async def test_search_all_isolates_invalid_doc_id_in_batch(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC7"
    await server.airiti_search_all("x", export_format="endnote")
    d = _only_run_dir(root)
    text = (d / "export.enw").read_text(encoding="utf-8")
    assert text.count("%0 ") == 44 and "%T DOC7" + CRLF not in text and "%T DOC6" in text  # an EndNote file, one record per id
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "成功 44 篇" in log and "匯出失敗" in log and "DOC7" in log


async def test_search_all_without_export_format_does_not_export(fake_site):
    state, root = fake_site
    await server.airiti_search_all("x")
    assert state["export_calls"] == [] and not list(_only_run_dir(root).glob("export.*"))


async def test_search_all_rejects_bad_export_format_before_any_request(fake_site):
    state, root = fake_site
    with pytest.raises(ToolError):
        await server.airiti_search_all("x", export_format="pdf")
    assert list(root.iterdir()) == []


async def test_export_gives_up_after_three_failures_and_separates_unattempted_from_failed(fake_site, monkeypatch):
    state, root = fake_site
    calls = []

    async def down(ids, site_type):
        calls.append(len(ids))
        if len(calls) >= 2:  # first batch fine, then the network stays down
            raise AiritiNetworkError("boom")
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": "TY  - JOUR" + CRLF + "ER  - " + CRLF}})

    monkeypatch.setattr(server.client, "fetch_cite_export", down)
    first = await server.airiti_search_all("x", export_format="ris")
    d = _only_run_dir(root)
    assert "因錯誤暫停" in first and not (d / "search_log.txt").exists()
    await server.airiti_search_all(resume=d.name)
    out = await server.airiti_search_all(resume=d.name)
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "匯出中斷】連續失敗 3 次後放棄" in log and "因中斷未處理】25 篇" in log
    assert "匯出失敗" not in log and "DOC22" not in log  # untried ids are not listed as invalid
    assert "成功 20 篇" in log and "因中斷未處理" in out


async def test_transient_export_error_is_retried_on_resume_without_duplicates(fake_site, monkeypatch):
    state, root = fake_site
    calls = []
    orig = server.client.fetch_cite_export

    async def hiccup(ids, site_type):
        calls.append(list(ids))
        if len(calls) == 2:  # only the second batch call fails, once
            raise AiritiNetworkError("blip")
        return await orig(ids, site_type)

    monkeypatch.setattr(server.client, "fetch_cite_export", hiccup)
    first = await server.airiti_search_all("x", export_format="ris")
    d = _only_run_dir(root)
    assert "因錯誤暫停" in first
    second = await server.airiti_search_all(resume=d.name)
    assert "因錯誤暫停" not in second and "已暫停" not in second
    titles = [l for l in (d / "export.ris").read_bytes().decode("utf-8").split(CRLF) if l.startswith("T1  - ")]
    assert len(titles) == 45 == len(set(titles))
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "成功 45 篇" in log and "因中斷未處理" not in log and "匯出中斷" not in log


async def test_isolating_a_bad_id_costs_few_requests(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC7"
    await server.airiti_search_all("x", export_format="ris")
    # batch 1: 2 tries of the whole batch + 20 single tries (one each); batch 2: 1 call
    assert len(state["export_calls"]) == 2 + 20 + 1 + 1


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
    assert "共 1 篇" in one and state["export_calls"][-1] == ["DOC9"] and "@article{DOC9" in one  # and it IS BibTeX


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
    state["step"] = 50.0  # every page "takes" 50 s; budget is 10 s. Page 1 is fetched before the run exists
    first = await server.airiti_search_all("x", time_budget_s=10)
    d = _only_run_dir(root)
    assert "已暫停" in first and f'resume="{d.name}"' in first and "抓取結果頁 1/3" in first  # page 1 used the budget
    assert (d / "state.json").exists() and (d / "results.csv").exists()
    assert not (d / "search_log.txt").exists()  # the log is written when the run is complete
    assert len(_rows(d)) == 1 + 20  # provisional list so far

    second = await server.airiti_search_all(resume=d.name, time_budget_s=10)
    assert "已暫停" in second and "抓取結果頁 2/3" in second
    third = await server.airiti_search_all(resume=d.name, time_budget_s=10)
    assert "已暫停" not in third and "共 45 筆" in third
    assert state["pages"] == [1, 2, 3]  # nothing fetched twice
    assert len(_rows(d)) == 1 + 45
    assert "分 3 次呼叫完成" in (d / "search_log.txt").read_text(encoding="utf-8")

    again = await server.airiti_search_all(resume=d.name)  # finished run: just shows the result again
    assert "共 45 筆" in again and state["pages"] == [1, 2, 3]


async def test_pauses_in_export_then_resumes_without_duplicates(fake_site):
    state, root = fake_site
    state["export_step"] = 50.0
    first = await server.airiti_search_all("x", export_format="ris", time_budget_s=10)
    d = _only_run_dir(root)
    assert "已暫停" in first and "匯出書目 20/45" in first
    assert (d / "export.ris").read_bytes().decode("utf-8").count("TY  - ") == 20  # 18 articles + 2 theses in the first batch

    second = first
    for _ in range(5):  # 3 batches of 20/20/5, each "takes" 50 s against a 10 s budget: one batch per call
        second = await server.airiti_search_all(resume=d.name, time_budget_s=10)
        if "已暫停" not in second:
            break
    assert "已暫停" not in second
    text = (d / "export.ris").read_bytes().decode("utf-8")
    titles = [l for l in text.split(CRLF) if l.startswith("T1  - ")]
    assert len(titles) == 45 == len(set(titles))  # every exportable result exactly once
    assert "成功 45 篇" in (d / "search_log.txt").read_text(encoding="utf-8")
    assert [len(c) for c in state["export_calls"]] == [20, 20, 5]


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


def _it(doc_id, title, authors, source="期刊A 12卷1期 (2025/04) Pp. 39-70"):
    return SearchItem(doc_id, title, authors, source, "期刊", f"https://x/{doc_id}", False)


def test_duplicate_groups_flags_same_title_and_authors_only():
    a = ["黃甲(Jia Huang)", "邱乙(Yi Chiou)"]
    items = [
        _it("P1", "以鷹架融入教學：個案研究", a),
        _it("P2", "以鷹架融入教學: 個案研究", ["黃甲", "邱乙(Y. Chiou)"]),  # punctuation / romanisation differ
        _it("P3", "以鷹架融入教學：個案研究", ["王丙"]),  # same title, different author -> a different paper
        _it("P4", "完全不同的篇名", a),
        _it("P5", "Scaffolding in Class", ["Lee"]),
        _it("P6", "SCAFFOLDING  in class", ["lee"]),  # case / spacing
        _it("P7", "", ["x"]),
        _it("P8", "", ["x"]),  # empty titles are never grouped
    ]
    g = duplicate_groups(items)
    assert g == {"P1": "D1", "P2": "D1", "P5": "D2", "P6": "D2"}


def test_csv_marks_preprint_and_duplicate_group_without_dropping_rows():
    final = _it("P1", "同一篇", ["甲"], "《期刊》 12卷1期 (2025/04) Pp. 39-70")
    pre = _it("P2", "同一篇", ["甲"], "《期刊》 預刊文章 Pp. 1-32")
    other = _it("P3", "別篇", ["乙"])
    assert not is_preprint(final) and is_preprint(pre)
    rows = list(csv.reader(io.StringIO(build_csv([final, pre, other]), newline="")))
    assert rows[0][-2:] == ["預刊", "疑似重複組"] and len(rows) == 4  # nothing removed
    assert [r[-2:] for r in rows[1:]] == [["", "D1"], ["Y", "D1"], ["", ""]]


def test_log_reports_duplicate_groups_and_preprints():
    from datetime import datetime

    items = [_it("P1", "同一篇", ["甲"]), _it("P2", "同一篇", ["甲"], "《期刊》 預刊文章 Pp. 1-32"), _it("P3", "別篇", ["乙"])]
    log = build_log(query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=3, fetched=3, unique=3,
                    pages_fetched=1, page_size=20, cap=100, items=items)
    assert "未自動移除）：1 組、涉及 2 筆" in log and "預刊文章" in log and "：1 筆，見 results.csv「預刊」欄" in log
    clean = build_log(query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=1, fetched=1, unique=1,
                      pages_fetched=1, page_size=20, cap=100, items=[_it("P3", "別篇", ["乙"])])
    assert "：0 組、涉及 0 筆" in clean


async def test_search_all_summary_mentions_suspected_duplicates(fake_site, monkeypatch):
    state, root = fake_site
    pair = [_it("P1", "同一篇", ["甲"]), _it("P2", "同一篇", ["甲"], "《期刊》 預刊文章 Pp. 1-32")]
    monkeypatch.setattr(server.parsers, "parse_search", lambda html: SearchPage(total=2, items=pair))
    out = await server.airiti_search_all("x")
    assert "疑似重複 1 組、涉及 2 筆" in out
    d = _only_run_dir(root)
    assert len(_rows(d)) == 1 + 2  # both rows kept
    assert _rows(d)[1][-1] == "D1" and _rows(d)[2][-2:] == ["Y", "D1"]


async def test_first_page_time_counts_against_the_budget(fake_site):
    state, root = fake_site
    state["step"] = 60.0
    out = await server.airiti_search_all("x", time_budget_s=100)
    # page 1 took 60 s, leaving 40 s: page 2 starts (60 < 100) and ends at 120, page 3 must wait
    assert "已暫停" in out and "抓取結果頁 2/3" in out and state["pages"] == [1, 2]


async def test_isolating_a_bad_id_respects_the_time_budget(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC7"
    state["export_step"] = 3.0
    first = await server.airiti_search_all("x", export_format="ris", time_budget_s=10)
    d = _only_run_dir(root)
    assert "已暫停" in first
    assert len(state["export_calls"]) <= 6  # not the ~22 calls (66 s) the old nested loop made in one go
    for _ in range(40):
        if "已暫停" not in await server.airiti_search_all(resume=d.name, time_budget_s=10):
            break
    assert len(state["export_calls"]) == 2 + 20 + 1 + 1  # pausing costs no extra requests
    text = (d / "export.ris").read_bytes().decode("utf-8")
    titles = [l for l in text.split(CRLF) if l.startswith("T1  - ")]
    assert len(titles) == 44 == len(set(titles)) and "T1  - DOC7" + CRLF not in text
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "成功 44 篇" in log and "匯出失敗】1 篇" in log and "DOC7" in log


async def test_abandoned_run_never_reports_progress_complete(fake_site, tmp_path):
    state, root = fake_site
    st, _ = await harvest.start_run("x", 1000, "")
    folder = tmp_path / "r"
    folder.mkdir()
    state["fail_page"] = 2
    seen = []

    async def prog(done, total, message):
        seen.append((done, total, message))

    for _ in range(3):
        finished = await harvest.advance(st, folder, 90, prog)
    assert finished and st.gave_up
    assert not any(m == "完成" for _, _, m in seen)


async def test_concurrent_resume_of_one_run_is_refused_not_duplicated(fake_site, monkeypatch):
    import asyncio

    state, root = fake_site
    state["export_step"] = 50.0
    await server.airiti_search_all("x", export_format="ris", time_budget_s=10)
    d = _only_run_dir(root)
    state["export_step"] = 0.0
    orig = server.client.fetch_cite_export

    async def yielding(ids, site_type):
        await asyncio.sleep(0.01)  # a real network call suspends the coroutine, letting the other call in
        return await orig(ids, site_type)

    monkeypatch.setattr(server.client, "fetch_cite_export", yielding)
    outs = await asyncio.gather(server.airiti_search_all(resume=d.name), server.airiti_search_all(resume=d.name))
    assert sum("正在另一個呼叫裡執行" in o for o in outs) == 1
    titles = [l for l in (d / "export.ris").read_bytes().decode("utf-8").split(CRLF) if l.startswith("T1  - ")]
    assert len(titles) == 45 == len(set(titles))


def test_resume_refuses_a_link_that_points_outside_the_export_folder(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    root = tmp_path / "exports"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "state.json").write_text("{}", encoding="utf-8")
    link = root / "20260101_000000_victim"
    try:
        if sys.platform == "win32":  # a junction needs no admin rights
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            assert r.returncode == 0
        else:
            os.symlink(outside, link, target_is_directory=True)
    except (OSError, AssertionError):
        pytest.skip("cannot create a directory link here")
    monkeypatch.setenv("AIRITI_EXPORT_DIR", str(root))
    with pytest.raises(AiritiError, match="不在匯出資料夾"):
        harvest.resolve_run_folder(link.name)


def test_duplicate_groups_ignores_records_without_authors_and_folds_full_width():
    # editorials in different journals: same title, no author -> not one paper
    assert duplicate_groups([_it("1", "編者的話", [], "A期刊 2010"), _it("2", "編者的話", [], "B期刊 2024")]) == {}
    # full-width vs ASCII letters/digits are the same title
    g = duplicate_groups([_it("1", "5G通訊技術之應用", ["甲"]), _it("2", "５Ｇ通訊技術之應用", ["甲"], "預刊文章")])
    assert g == {"1": "D1", "2": "D1"}


async def test_resume_drops_a_batch_that_was_written_but_never_saved(fake_site):
    state, root = fake_site
    state["export_step"] = 50.0
    await server.airiti_search_all("x", export_format="ris", time_budget_s=10)  # batch 1 written and saved
    d = _only_run_dir(root)
    path = d / "export.ris"
    saved = path.read_bytes()
    # simulate a hard kill between "append batch 2" and "save state": batch 2 is on disk, state does not know
    path.write_bytes(saved + saved)
    state["export_step"] = 0.0
    await server.airiti_search_all(resume=d.name, time_budget_s=10)
    titles = [l for l in path.read_bytes().decode("utf-8").split(CRLF) if l.startswith("T1  - ")]
    assert len(titles) == 45 == len(set(titles))  # every id exactly once; the unsaved copy was dropped


async def test_old_state_without_export_bytes_is_never_truncated(fake_site):
    state, root = fake_site
    state["export_step"] = 50.0
    await server.airiti_search_all("x", export_format="ris", time_budget_s=10)
    d = _only_run_dir(root)
    # a state file written before export_bytes / version existed
    data = json.loads((d / "state.json").read_text(encoding="utf-8"))
    del data["export_bytes"], data["version"]
    (d / "state.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = (d / "export.ris").read_bytes()
    state["export_step"] = 0.0
    await server.airiti_search_all(resume=d.name, time_budget_s=10)
    after = (d / "export.ris").read_bytes()
    assert after.startswith(before) and after.decode("utf-8").count("TY  - ") == 45  # nothing was wiped


def test_state_file_version_is_checked(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    base = {"query": "q", "max_total": 10, "export_key": "", "started": "2026-01-01T00:00:00+08:00"}
    (folder / "state.json").write_text(json.dumps({**base, "version": 99}), encoding="utf-8")
    with pytest.raises(AiritiError, match="版本 99"):
        harvest.load_state(folder)
    (folder / "state.json").write_text(json.dumps(base), encoding="utf-8")  # no version = old file = version 1
    assert harvest.load_state(folder).version == 1
    (folder / "state.json").write_text(json.dumps({**base, "no_such_field": 1}), encoding="utf-8")
    with pytest.raises(AiritiError, match="讀不到"):
        harvest.load_state(folder)
    (folder / "state.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(AiritiError, match="讀不到"):
        harvest.load_state(folder)


def test_cite_export_keeps_the_trailing_space_of_the_ris_end_tag():
    from airiti_mcp.cite_export import parse_cite_export

    body = json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": "TY  - JOUR" + CRLF + "ER  - " + CRLF + CRLF}})
    assert parse_cite_export(body) == "TY  - JOUR" + CRLF + "ER  - "  # was "ER  -" before


async def test_merged_ris_keeps_every_end_tag_intact(fake_site):
    state, root = fake_site
    await server.airiti_search_all("x", export_format="ris")
    text = (_only_run_dir(root) / "export.ris").read_bytes().decode("utf-8")
    assert text.count("ER  - " + CRLF) == text.count("TY  - ") == 45  # last record of each batch included


async def test_export_citation_save_writes_a_file_and_keeps_content_out_of_the_reply(fake_site):
    state, root = fake_site
    out = await server.airiti_export_citation(["DOC1", "DOC2"], "ris", save=True)
    assert "TY  - JOUR" not in out and "共 2 篇" in out and "已存檔" in out
    d = _only_run_dir(root)
    assert str(d / "export.ris") in out and d.parent == root and "export_2" in d.name
    raw = (d / "export.ris").read_bytes().decode("utf-8")
    assert raw.count("TY  - JOUR") == 2 and raw.count("ER  - " + CRLF) == 2 and raw.endswith(CRLF)
    assert not (d / "state.json").exists()  # a one-shot export is not a resumable run
    # a different export (other id count -> other folder name) gets its own folder and leaves the first alone
    # (the real same-name collision is covered by test_export_folder_name_collision_gets_a_suffix)
    await server.airiti_export_citation(["DOC3"], "bibtex", save=True)
    assert len(_run_dirs(root)) == 2
    assert (d / "export.ris").read_bytes().decode("utf-8") == raw
    # default behaviour is unchanged: the text comes back in the reply and nothing new is written
    inline = await server.airiti_export_citation(["DOC1"], "ris")
    assert "TY  - JOUR" in inline and len(_run_dirs(root)) == 2


async def test_export_citation_save_failure_writes_nothing(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC1"
    with pytest.raises(ToolError, match="處理失敗"):
        await server.airiti_export_citation(["DOC1"], "ris", save=True)
    assert _run_dirs(root) == []


def test_absolutize_urls_touches_only_relative_ur_lines():
    from airiti_mcp.cite_export import absolutize_urls

    base = "https://www.airitilibrary.com"
    ris = (
        "TY  - JOUR" + CRLF + "UR  - Article/Detail?docID=P1-2" + CRLF + "ER  - " + CRLF + CRLF
        + "TY  - JOUR" + CRLF + "UR  - https://doi.org/10.1/x" + CRLF + "AB  - see Article/Detail?docID=Z in the text" + CRLF + "ER  - " + CRLF
    )
    out = absolutize_urls(ris, base)
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=P1-2" + CRLF in out
    assert "UR  - https://doi.org/10.1/x" in out and "AB  - see Article/Detail?docID=Z in the text" in out
    assert out.count("ER  - " + CRLF) == 2 and out.count(CRLF) == ris.count(CRLF)  # nothing else moved
    assert absolutize_urls(out, base) == out  # idempotent
    for other in ("@article{a,\r\nDOI ={10.1/x},\r\n}", "%0 Journal Article" + CRLF + "%T t" + CRLF):
        assert absolutize_urls(other, base) == other  # BibTeX / EndNote are never changed


async def test_export_path_completes_relative_urls_end_to_end(fake_site, monkeypatch):
    state, root = fake_site

    async def no_doi_site(ids, site_type):
        content = "".join("TY  - JOUR" + CRLF + "T1  - " + i + CRLF + "UR  - Article/Detail?docID=" + i + CRLF + "ER  - " + CRLF + CRLF for i in ids)
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    monkeypatch.setattr(server.client, "fetch_cite_export", no_doi_site)
    out = await server.airiti_export_citation(["DOC1"], "ris")
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=DOC1" in out
    await server.airiti_search_all("x", export_format="ris")
    d = [p for p in root.iterdir() if p.is_dir()][0]
    merged = (d / "export.ris").read_bytes().decode("utf-8")
    assert merged.count("UR  - https://www.airitilibrary.com/Article/") == 45 and "UR  - Article/" not in merged
    assert "已補成完整網址" in (d / "search_log.txt").read_text(encoding="utf-8")


async def test_export_folder_name_collision_gets_a_suffix(fake_site, monkeypatch):
    from datetime import datetime as real_datetime

    class Frozen(real_datetime):  # two saves in "the same second" without depending on the wall clock
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 11, 6, 15, 0, 0)

    monkeypatch.setattr(server, "datetime", Frozen)
    state, root = fake_site
    first = await server.airiti_export_citation(["DOC1"], "ris", save=True)
    second = await server.airiti_export_citation(["DOC2"], "ris", save=True)  # same id count -> same folder name
    names = sorted(p.name for p in _run_dirs(root))
    assert len(names) == 2 and names[0].endswith("_export_1") and names[1].endswith("_export_1_2")
    assert (root / names[0] / "export.ris").read_bytes().decode("utf-8").count("T1  - DOC1") == 1  # not overwritten
    assert (root / names[1] / "export.ris").read_bytes().decode("utf-8").count("T1  - DOC2") == 1
    assert names[0] in first and names[1] in second


async def test_export_citation_save_cleans_up_when_writing_fails(fake_site, monkeypatch):
    from pathlib import Path

    state, root = fake_site
    real_open = Path.open

    def full_disk(self, mode="r", *a, **k):
        if "w" in mode and self.name == "export.ris":
            raise OSError(28, "No space left on device")
        return real_open(self, mode, *a, **k)

    monkeypatch.setattr(Path, "open", full_disk)
    with pytest.raises(ToolError, match="No space left on device.*AIRITI_EXPORT_DIR"):  # the model gets the reason + a way out
        await server.airiti_export_citation(["DOC1"], "ris", save=True)
    assert _run_dirs(root) == []  # no empty folder left behind


async def test_save_true_writes_absolute_urls_to_disk(fake_site, monkeypatch):
    state, root = fake_site

    async def no_doi(ids, site_type):
        content = "".join("TY  - JOUR" + CRLF + "UR  - Article/Detail?docID=" + i + CRLF + "ER  - " + CRLF + CRLF for i in ids)
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    monkeypatch.setattr(server.client, "fetch_cite_export", no_doi)
    await server.airiti_export_citation(["DOC1"], "ris", save=True)
    raw = (_only_run_dir(root) / "export.ris").read_bytes().decode("utf-8")
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=DOC1" + CRLF in raw and "UR  - Article/" not in raw


async def test_only_ris_content_gets_urls_rewritten(fake_site, monkeypatch):
    state, root = fake_site
    # a BibTeX / EndNote body whose text happens to contain a line shaped like an RIS UR line
    odd = "@article{a," + CRLF + "abstract ={first" + CRLF + "UR  - Article/Detail?docID=Z" + CRLF + "},}" + CRLF

    async def serve(ids, site_type):
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": odd}})

    monkeypatch.setattr(server.client, "fetch_cite_export", serve)
    bib, _ = await harvest.export_ids(["DOC1"], "BibTeX")
    enw, _ = await harvest.export_ids(["DOC1"], "Endnote")
    ris, _ = await harvest.export_ids(["DOC1"], "RIS")
    assert "UR  - Article/Detail?docID=Z" in bib and "UR  - Article/Detail?docID=Z" in enw  # untouched
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=Z" in ris


async def test_log_has_no_url_note_when_nothing_was_exported(fake_site, monkeypatch):
    state, root = fake_site
    state["total"] = 1

    async def refuse(ids, site_type):
        return json.dumps({"ReturnValue": 1, "ReturnMessage": "no", "ReturnData": None})

    monkeypatch.setattr(server.client, "fetch_cite_export", refuse)
    await server.airiti_search_all("x", export_format="ris")
    log = (_only_run_dir(root) / "search_log.txt").read_text(encoding="utf-8")
    assert "未產生檔案" in log and "已補成完整網址" not in log


async def test_empty_page_in_the_middle_is_retried_in_the_same_call(fake_site):
    state, root = fake_site
    state["empty"] = {2: 1}  # the site says "0 筆" for page 2 once, then behaves
    out = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    assert len(_rows(d)) == 1 + 45 and "已暫停" not in out and "因錯誤暫停" not in out
    assert state["pages"] == [1, 2, 2, 3] and state["sleeps"] == [3.0]  # retried after a 3 s back-off, then moved on
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "頁面重試：1 次" in log and "不完整" not in log and "完整性核對：預期 45 筆，取得不重複 45 筆 → 完整" in log


async def test_persistent_empty_page_pauses_and_resumes_instead_of_ending_the_run(fake_site):
    state, root = fake_site
    state["empty"] = {2: 99}
    first = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    assert "因錯誤暫停" in first and "多半是暫時性的空頁" in first and not (d / "search_log.txt").exists()
    assert state["sleeps"] == [3.0, 6.0]
    state["empty"] = {}  # the site recovers
    second = await server.airiti_search_all(resume=d.name)
    assert "因錯誤暫停" not in second and len(_rows(d)) == 1 + 45  # pages 2 and 3 were NOT lost
    assert state["pages"].count(1) == 1  # page 1 is never fetched again
    assert "不完整" not in (d / "search_log.txt").read_text(encoding="utf-8")


async def test_a_budget_too_short_for_the_retries_still_counts_a_strike_and_never_pauses_forever(fake_site):
    state, root = fake_site
    state["step"] = 2.0  # slow site + 10 s budget: the 6 s back-off never fits
    state["empty"] = {2: 10**6}
    out = await server.airiti_search_all("x", time_budget_s=10)
    d = _only_run_dir(root)
    assert "因錯誤暫停" in out and harvest.load_state(d).error_streak == 1
    for _ in range(6):
        if harvest.load_state(d).finished:
            break
        await server.airiti_search_all(resume=d.name, time_budget_s=10)
    st = harvest.load_state(d)
    assert st.finished and st.harvest_error  # three strikes, then it gives up instead of looping forever
    assert "不完整" in (d / "search_log.txt").read_text(encoding="utf-8") and len(_rows(d)) == 1 + 20


async def test_a_flaky_empty_first_page_is_verified_before_saying_no_results(fake_site):
    state, root = fake_site
    state["empty"] = {1: 1}
    out = await server.airiti_search_all("x")
    assert "共 45 筆" in out and state["pages"][:2] == [1, 1]
    # genuinely nothing: asked three times, then believed
    state["total"] = 0
    state["pages"].clear()
    out2 = await server.airiti_search_all("沒有這個詞")
    assert "0 筆" in out2 and "已重試 2 次" in out2 and state["pages"] == [1, 1, 1]


async def test_single_page_search_verifies_a_zero_before_reporting_no_results(fake_site):
    state, root = fake_site
    state["empty"] = {1: 1}
    ok = await server.airiti_search("x", 1, 10)
    assert "共 45 筆" in ok and "本頁 10 筆" in ok and state["pages"] == [1, 1]  # 10 per page, as asked
    state["pages"].clear()
    state["empty"] = {1: 99}
    zero = await server.airiti_search("x", 1, 10)
    assert "已重試 2 次仍是 0 筆" in zero and state["pages"] == [1, 1, 1]
    state["pages"].clear()
    beyond = await server.airiti_search("x", 9, 10)  # a genuinely out-of-range page: one retry only
    assert "頁碼超出總頁數" in beyond and "已重試 1 次" in beyond and state["pages"] == [9, 9]


async def test_completeness_check_reports_a_record_lost_at_a_page_boundary(fake_site, monkeypatch):
    state, root = fake_site
    pages = {  # a tie swapped across the boundary between two requests: 19 shows up twice, 20 never
        "1": SearchPage(total=45, items=[_item(n) for n in range(0, 20)]),
        "2": SearchPage(total=45, items=[_item(19)] + [_item(n) for n in range(21, 40)]),
        "3": SearchPage(total=45, items=[_item(n) for n in range(40, 45)]),
    }
    monkeypatch.setattr(server.parsers, "parse_search", lambda html: pages[html])
    out = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "移除 1 筆，剩 44 筆" in log and "預期 45 筆，取得不重複 44 筆 → 缺 1 筆" in log and "同分" not in log.split("缺 1 筆")[0]
    assert "【完整性】預期 45 筆，只取得 44 筆（缺 1 筆）" in out


def _tied_boundary_site(monkeypatch, tails, heads):
    """45 records, 20 per page. Records 19 and 20 are tied in rank and straddle the page 1|2 boundary; the site
    orders them anew on every request: request n of page 1 ends with tails[n], request n of page 2 starts with heads[n]."""
    calls = {"1": 0, "2": 0}

    def parse(html):
        n = calls.get(html, 0)
        calls[html] = n + 1
        if html == "1":
            tail = tails[min(n, len(tails) - 1)]
            return SearchPage(total=45, items=[_item(i) for i in range(0, 19)] + [_item(tail)])
        if html == "2":
            head = heads[min(n, len(heads) - 1)]
            return SearchPage(total=45, items=[_item(head)] + [_item(i) for i in range(21, 40)])
        return SearchPage(total=45, items=[_item(i) for i in range(40, 45)])

    monkeypatch.setattr(server.parsers, "parse_search", parse)


async def test_tied_records_lost_at_a_page_boundary_are_recovered_by_refetching(fake_site, monkeypatch):
    state, root = fake_site
    # harvest: 19 shows at the end of page 1 AND the start of page 2, 20 nowhere. Round 1 repeats the mistake,
    # round 2 gets a consistent pair (page 1 ends with 20, page 2 starts with 19) which contains the lost record.
    _tied_boundary_site(monkeypatch, tails=[19, 19, 20], heads=[19, 19, 19])
    out = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    ids = [r[1] for r in _rows(d)[1:]]
    assert len(ids) == 45 == len(set(ids)) and "DOC20" in ids and ids.index("DOC20") < ids.index("DOC21")
    assert state["pages"] == [1, 2, 3, 1, 2, 1, 2]  # only the two pages around the boundary were asked again, twice
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "邊界修補：1 處頁邊界出現重複" in log and "補回 1 筆" in log and "預期 45 筆，取得不重複 45 筆 → 完整" in log
    assert "本次實際取得：45 筆" in log and "去除重複 DocID：移除 1 筆，剩 45 筆" in log  # 45 page rows, +1 recovered, -1 repeat
    assert ids[18:22] == ["DOC18", "DOC19", "DOC20", "DOC21"]
    assert "【完整性】" not in out and "邊界修補" in out


async def test_boundary_repair_gives_up_after_three_rounds_and_still_reports_the_loss(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19], heads=[19])  # the site never gets it right
    out = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    assert len(_rows(d)) == 1 + 44 and state["pages"] == [1, 2, 3] + [1, 2] * 3  # bounded: 3 rounds, no more
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert "缺 1 筆" in log and "1 處在 3 輪內沒有得到一致的結果，可能仍缺記錄" in log and "【完整性】" in out


async def test_a_clean_harvest_makes_no_repair_requests(fake_site):
    state, root = fake_site
    await server.airiti_search_all("x")
    assert state["pages"] == [1, 2, 3]
    assert "邊界修補" not in (_only_run_dir(root) / "search_log.txt").read_text(encoding="utf-8")


async def test_repair_survives_a_pause_and_resume(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19, 20], heads=[19, 19])
    state["step"] = 6.0  # every request "takes" 6 s; budget 10 s
    first = await server.airiti_search_all("x", time_budget_s=10)
    d = _only_run_dir(root)
    for _ in range(30):
        if "已暫停" not in first:
            break
        first = await server.airiti_search_all(resume=d.name, time_budget_s=10)
    ids = [r[1] for r in _rows(d)[1:]]
    assert len(ids) == 45 == len(set(ids)) and "DOC20" in ids


async def test_a_record_recovered_at_the_head_of_the_next_page_sorts_back_to_its_rank(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19], heads=[19, 19, 20])  # DOC20 turns up as the FIRST row of page 2
    await server.airiti_search_all("x")
    ids = [r[1] for r in _rows(_only_run_dir(root))[1:]]
    assert len(ids) == 45 and ids[18:23] == ["DOC18", "DOC19", "DOC20", "DOC21", "DOC22"]  # not parked after DOC39


async def test_the_cap_keeps_a_recovered_top_ranked_record(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19], heads=[19, 19, 20])
    await server.airiti_search_all("x", max_total=25)
    ids = [r[1] for r in _rows(_only_run_dir(root))[1:]]
    assert ids == [f"DOC{i}" for i in range(25)]  # the old order cut DOC20 and kept DOC25 instead


async def test_completeness_is_not_hidden_when_the_cap_is_below_the_total(fake_site, monkeypatch):
    state, root = fake_site
    pages = {  # reported total 100, cap 25 -> two pages; 19 twice, 20 never, and the site never gets it right
        "1": SearchPage(total=100, items=[_item(i) for i in range(0, 20)]),
        "2": SearchPage(total=100, items=[_item(19)] + [_item(i) for i in range(21, 40)]),
    }
    monkeypatch.setattr(server.parsers, "parse_search", lambda html: pages[html])
    out = await server.airiti_search_all("x", max_total=25)
    log = (_only_run_dir(root) / "search_log.txt").read_text(encoding="utf-8")
    assert "預期 40 筆，取得不重複 39 筆 → 缺 1 筆" in log and "總筆數 100、上限 25" in log  # was "預期 25 ... → 完整"
    assert "【完整性】預期 40 筆，只取得 39 筆（缺 1 筆）" in out


def test_log_flags_more_records_than_the_site_reported():
    from datetime import datetime

    log = build_log(query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=40, fetched=43, deduped=43,
                    unique=43, pages_fetched=3, page_size=20, cap=100, items=[_it(str(i), "t" + str(i), ["a"]) for i in range(43)], expected=40)
    assert "取得不重複 43 筆 → 多 3 筆（站方的資料可能在抓取期間更新）" in log


async def test_a_network_failure_during_boundary_repair_pauses_and_resume_finishes_the_repair(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19, 19, 20], heads=[19])
    down = {"on": True}
    orig = server.client.fetch_search_html

    async def flaky(q, page, size):
        if down["on"] and len(state["pages"]) >= 3:  # pages 1,2,3 are the harvest; afterwards the network is down
            state["pages"].append(page)
            raise AiritiNetworkError("down")
        return await orig(q, page, size)

    monkeypatch.setattr(server.client, "fetch_search_html", flaky)
    first = await server.airiti_search_all("x")
    d = _only_run_dir(root)
    st = harvest.load_state(d)
    assert "因錯誤暫停" in first and "邊界修補" in first and not st.finished and st.error_streak == 1
    assert not (d / "search_log.txt").exists()  # the run is not over, so no final log yet
    down["on"] = False
    second = await server.airiti_search_all(resume=d.name)
    ids = [r[1] for r in _rows(d)[1:]]
    assert "因錯誤暫停" not in second and len(ids) == 45 == len(set(ids)) and "DOC20" in ids
    assert "邊界修補" in (d / "search_log.txt").read_text(encoding="utf-8")


async def test_boundary_repair_gives_up_after_three_connection_failures_but_the_run_still_finishes(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19], heads=[19])
    orig = server.client.fetch_search_html

    async def dies(q, page, size):
        if len(state["pages"]) >= 3:
            state["pages"].append(page)
            raise AiritiNetworkError("down")
        return await orig(q, page, size)

    monkeypatch.setattr(server.client, "fetch_search_html", dies)
    await server.airiti_search_all("x")
    d = _only_run_dir(root)
    await server.airiti_search_all(resume=d.name)
    out = await server.airiti_search_all(resume=d.name)  # third strike
    st = harvest.load_state(d)
    log = (d / "search_log.txt").read_text(encoding="utf-8")
    assert st.finished and st.error_streak == 0 and "因連線問題中止" in log and "沒有得到一致" not in log
    assert "不完整" not in out and len(_rows(d)) == 1 + 44  # the harvest itself is kept; the loss is reported, not hidden
    assert "缺 1 筆" in log


async def test_pause_during_boundary_repair_says_so(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19], heads=[19])
    state["step"] = 6.0
    outs = [await server.airiti_search_all("x", time_budget_s=10)]
    d = _only_run_dir(root)
    for _ in range(40):
        if harvest.load_state(d).finished:
            break
        outs.append(await server.airiti_search_all(resume=d.name, time_budget_s=10))
    assert any("邊界修補中（結果頁已抓完 3/3）" in o for o in outs)
    assert not any("抓取結果頁 3/3" in o for o in outs)


# ======================================================== batch 1: hardening and decisions
async def test_a_big_export_is_saved_to_a_file_automatically(fake_site, monkeypatch):
    state, root = fake_site
    monkeypatch.setattr(server, "EXPORT_INLINE_LIMIT", 50)
    big = await server.airiti_export_citation(["DOC1", "DOC2", "DOC3"], "ris")  # three records = 100 characters > 50
    assert "自動存檔" in big and "內容沒有放進對話" in big and "TY  - " not in big
    d = _only_run_dir(root)
    assert (d / "export.ris").read_bytes().decode("utf-8").count("TY  - JOUR") == 3
    small = await server.airiti_export_citation(["DOC4"], "ris")  # one record = 32 characters stays in the reply
    assert "TY  - JOUR" in small and "自動存檔" not in small and len(_run_dirs(root)) == 1


async def test_repair_that_keeps_failing_on_the_second_page_counts_strikes_and_gives_up(fake_site, monkeypatch):
    state, root = fake_site
    _tied_boundary_site(monkeypatch, tails=[19], heads=[19])
    orig = server.client.fetch_search_html

    async def page2_down(q, page, size):
        if len(state["pages"]) >= 3 and page == 2:  # pages 1,2,3 were the harvest; then only page 2 fails
            state["pages"].append(page)
            raise AiritiNetworkError("down")
        return await orig(q, page, size)

    monkeypatch.setattr(server.client, "fetch_search_html", page2_down)
    await server.airiti_search_all("x")
    d = _only_run_dir(root)
    assert harvest.load_state(d).error_streak == 1
    await server.airiti_search_all(resume=d.name)
    assert harvest.load_state(d).error_streak == 2  # it used to be stuck at 1: page 1 answering reset the count
    await server.airiti_search_all(resume=d.name)
    st = harvest.load_state(d)
    assert st.finished and st.error_streak == 0 and st.repair_error  # third strike: the repair (not the run) gave up
    assert "因連線問題中止" in (d / "search_log.txt").read_text(encoding="utf-8")


async def test_the_export_gets_its_own_three_strikes_after_the_harvest_gave_up(fake_site, monkeypatch):
    state, root = fake_site
    state["fail_page"] = 2
    attempts = []

    async def export_down(ids, site_type):
        attempts.append(len(ids))
        raise AiritiNetworkError("blip")

    monkeypatch.setattr(server.client, "fetch_cite_export", export_down)
    await server.airiti_search_all("x", export_format="ris")
    d = _only_run_dir(root)
    await server.airiti_search_all(resume=d.name)
    await server.airiti_search_all(resume=d.name)  # third harvest strike: the harvest gives up and the export starts
    st = harvest.load_state(d)
    assert st.harvest_error and not st.finished and st.error_streak == 1 and len(attempts) == 1  # not abandoned at once
    for _ in range(4):
        if harvest.load_state(d).finished:
            break
        await server.airiti_search_all(resume=d.name)
    st = harvest.load_state(d)
    assert st.finished and st.export_problem and len(attempts) == 3  # three strikes of its own


async def test_a_finished_run_ends_with_the_done_message_and_progress_stays_strictly_increasing(fake_site, tmp_path):
    state, root = fake_site
    st, _ = await harvest.start_run("x", 1000, "ris")
    folder = tmp_path / "r"
    folder.mkdir()
    seen = []

    async def prog(done, total, message):
        seen.append((done, total, message))

    await harvest.advance(st, folder, 90, prog)
    values = [d for d, _, _ in seen]
    assert values == sorted(values) and len(set(values)) == len(values)
    assert seen[-1] == (6, 6, "完成") and all(v < 6 for v in values[:-1])  # only the final message reaches the total
    assert any(m == "已匯出 45/45 篇" for _, _, m in seen)  # the last export batch is reported too, not swallowed


async def test_every_tool_is_named_in_the_server_instructions():
    names = [t.name for t in await server.mcp.list_tools()]
    assert {"airiti_search", "airiti_abstract", "airiti_cite_apa", "airiti_search_all", "airiti_export_citation"} <= set(names)
    text = server.mcp.instructions
    for name in names:
        assert name in text, name  # a new tool that the instructions forget to mention fails here
    assert "resume" in text and "save=True" in text


async def test_a_disk_error_reaches_the_model_with_its_reason(fake_site, monkeypatch):
    from pathlib import Path

    from mcp.client import Client

    state, root = fake_site
    real_open = Path.open

    def full_disk(self, mode="r", *a, **k):
        if "w" in mode and self.name == "export.ris":
            raise OSError(28, "No space left on device")
        return real_open(self, mode, *a, **k)

    monkeypatch.setattr(Path, "open", full_disk)
    async with Client(server.mcp) as c:
        res = await c.call_tool("airiti_export_citation", {"doc_ids": ["DOC1"], "format": "ris", "save": True})
    assert "No space left on device" in str(res) and "AIRITI_EXPORT_DIR" in str(res)  # not just "Error executing tool"


def test_parse_cite_export_is_strict_about_what_counts_as_content():
    from airiti_mcp.cite_export import parse_cite_export

    def body(content):
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    assert parse_cite_export(body("   " + CRLF + "  ")) is None  # blank
    assert parse_cite_export(body(123)) is None and parse_cite_export(body(["a"])) is None and parse_cite_export(body(None)) is None
    assert parse_cite_export(body(CRLF + "TY  - JOUR" + CRLF + "ER  - " + CRLF)) == "TY  - JOUR" + CRLF + "ER  - "


async def test_two_runs_started_in_the_same_second_get_separate_folders(fake_site, monkeypatch):
    from datetime import datetime as real

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 11, 6, 15, 0, 0)

    monkeypatch.setattr(harvest, "datetime", Frozen)
    state, root = fake_site
    await server.airiti_search_all("x")
    await server.airiti_search_all("x")
    names = sorted(p.name for p in _run_dirs(root))
    assert len(names) == 2 and names[1] == names[0] + "_2"
    for n in names:
        assert harvest.load_state(root / n).finished  # the first run was not overwritten by the second


async def test_two_adjacent_tied_boundaries_are_both_repaired(fake_site, monkeypatch):
    state, root = fake_site
    calls = {"1": 0, "2": 0, "3": 0}
    # ties (19,20) at boundary 1|2 and (39,40) at boundary 2|3; the site re-rolls both on every request.
    # Boundary 2's lost record (40) only shows up in page 2's request number 4, i.e. in boundary 2's own repair.
    sched = {"1": [19, 19, 20], "2h": [19], "2t": [39, 39, 39, 39, 40], "3": [39]}

    def pick(key, n):
        return sched[key][min(n, len(sched[key]) - 1)]

    def parse(html):
        n = calls[html]
        calls[html] += 1
        if html == "1":
            return SearchPage(total=60, items=[_item(i) for i in range(0, 19)] + [_item(pick("1", n))])
        if html == "2":
            return SearchPage(total=60, items=[_item(pick("2h", n))] + [_item(i) for i in range(21, 39)] + [_item(pick("2t", n))])
        return SearchPage(total=60, items=[_item(pick("3", n))] + [_item(i) for i in range(41, 60)])

    monkeypatch.setattr(server.parsers, "parse_search", parse)
    await server.airiti_search_all("x")
    d = _only_run_dir(root)
    ids = [r[1] for r in _rows(d)[1:]]
    assert len(ids) == 60 and sorted(ids) == sorted(f"DOC{i}" for i in range(60))
    assert harvest.load_state(d).fixed_boundaries == [1, 2]  # repairing only the first boundary would leave DOC40 missing



def test_the_provisional_csv_of_a_paused_run_lists_recovered_records_in_rank_order():
    st = harvest.RunState("q", 100, "", "2026-01-01T00:00:00+08:00")
    # records 1,2 on page 1, record 3 on page 2; then boundary repair appended X (slot 2 of page 1) at the end
    st.items = [{"doc_id": "1"}, {"doc_id": "2"}, {"doc_id": "3"}, {"doc_id": "X"}]
    st.row_page = [1, 1, 2, 1]
    st.row_slot = [1, 2, 1, 2]
    assert [d["doc_id"] for d in st.unique_now()] == ["1", "2", "X", "3"]  # was 1, 2, 3, X
    st.row_page = []  # state written before row_page existed: fall back to arrival order
    assert [d["doc_id"] for d in st.unique_now()] == ["1", "2", "3", "X"]
