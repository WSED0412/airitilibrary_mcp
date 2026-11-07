"""server.py tool layer, offline: argument checks, error conversion, output assembly, entry point."""
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_parsers import DETAIL

from airiti_mcp import harvest, server, stdio
from airiti_mcp.errors import AiritiNetworkError
from airiti_mcp.parsers import Reference, parse_detail


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"query": "   "}, "query 不能是空的"),
        ({"query": "x", "page": 0}, "page 必須 >= 1"),
        ({"query": "x", "max_results": 0}, "max_results 必須介於 1 到 20"),
        ({"query": "x", "max_results": 25}, "max_results 必須介於 1 到 20"),
    ],
)
async def test_search_rejects_bad_arguments_before_any_request(kwargs, message, monkeypatch):
    async def must_not_be_called(*a, **k):
        raise AssertionError("no request may be made for a bad argument")

    monkeypatch.setattr(server.client, "fetch_search_html", must_not_be_called)
    with pytest.raises(ToolError, match=message):
        await server.airiti_search(**kwargs)


async def test_search_reports_the_network_error_after_all_three_tries(monkeypatch):
    sleeps = []

    async def no_sleep(seconds):
        sleeps.append(seconds)

    async def down(query, page, size):
        raise AiritiNetworkError("down")

    monkeypatch.setattr(harvest, "_sleep", no_sleep)
    monkeypatch.setattr(server.client, "fetch_search_html", down)
    with pytest.raises(ToolError, match="AiritiNetworkError: down"):
        await server.airiti_search("x")
    assert sleeps == [3.0, 6.0]  # page 1 is asked three times before the error is believed


async def test_abstract_renders_references_and_cited_by(monkeypatch):
    async def fake_detail(doc_id, with_references=False, with_cited_by=False):
        det = parse_detail(DETAIL, "ID")  # like the real parser: the lists exist only when they were asked for
        if with_references:
            det.references = [Reference("1", "文獻A", "REF1", "10.1/a"), Reference("2", "文獻B", "", "")]
        if with_cited_by:
            det.cited_by = []  # asked for, but there are none
        return det

    monkeypatch.setattr(server, "_detail", fake_detail)
    out = await server.airiti_abstract("ID", include_references=True, include_cited_by=True)
    assert "## 參考文獻（2 篇）" in out and "1. 文獻A" in out and "DocID：REF1" in out and "DOI：10.1/a" in out
    assert "2. 文獻B" in out
    assert "被引用記錄" in out and "（0 篇）" in out and "（無）" in out
    plain = await server.airiti_abstract("ID")  # both are off by default (they can be very long)
    assert "參考文獻" not in plain and "被引用記錄" not in plain


async def test_cite_apa_tool_formats_a_chinese_article_and_refuses_an_english_one(monkeypatch):
    det = parse_detail(DETAIL, "ID")

    async def fake_detail(*a, **k):
        return det

    monkeypatch.setattr(server, "_detail", fake_detail)
    assert (await server.airiti_cite_apa("ID")).startswith("蔡欣倫、王小明（2024）。題目。*期刊名*，*1*（3），19–57。")
    det.authors = ["Hsiu-Ho Wang"]
    with pytest.raises(ToolError, match="airiti_export_citation"):
        await server.airiti_cite_apa("ID")


def test_the_entry_point_runs_the_stdio_transport(monkeypatch):
    seen = []
    monkeypatch.setattr(stdio.mcp, "run", lambda transport: seen.append(transport))
    stdio.main()
    assert seen == ["stdio"]


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
