"""Hits the real site. Opt-in: set AIRITI_LIVE=1 (a few requests, ~1.5 s apart)."""
import os

import pytest

from airiti_mcp import server

pytestmark = [pytest.mark.skipif(not os.environ.get("AIRITI_LIVE"), reason="set AIRITI_LIVE=1"), pytest.mark.asyncio]


async def test_search():
    out = await server.airiti_search("鷹架 數學解題", 1, 10)
    assert "共 " in out and "P20240112001-N202410010002-00002" in out


async def test_search_plus_docid_roundtrip():
    out = await server.airiti_search("鷹架 數學解題", 1, 3)
    assert "49-72+171" in out and "%252b" not in out.lower()


async def test_search_zero():
    assert "0 筆" in await server.airiti_search("zzzxqjkw不存在的詞")  # also covered with hint below


async def test_abstract():
    out = await server.airiti_abstract("P20240112001-N202410010002-00002")
    for s in ("探究數學情意對工程數學學習成就的影響", "蔡欣倫", "10.30208/CGPS.202409_1(3).0002", "This study investigates"):
        assert s in out


async def test_abstract_bad_docid():
    with pytest.raises(Exception, match="不是文獻詳目頁"):
        await server.airiti_abstract("NOPE-does-not-exist")


async def test_abstract_references_opt_in():
    base = await server.airiti_abstract("P20240112001-N202410010002-00002")
    assert "## 參考文獻" not in base
    out = await server.airiti_abstract("P20240112001-N202410010002-00002", include_references=True)
    assert "## 參考文獻（20 篇）" in out and "楊嘉勝（2011）" in out and "不一致" not in out


async def test_few_results_hint_on_zero():
    out = await server.airiti_search("zzzxqjkw不存在的詞")
    assert "0 筆" in out and "提示：只找到 0 筆" in out


async def test_out_of_range_page_is_not_reported_as_no_results():
    out = await server.airiti_search("鷹架 數學解題", 50, 10)
    assert "不代表整個查詢沒有結果" in out and "確實沒有符合" not in out


async def test_abstract_extra_fields_and_cited_by():
    out = await server.airiti_abstract("P20240112001-N202410010002-00002")
    assert "並列篇名：Exploring Effects of Mathematical Affections" in out and "語言：en;zh-tw" in out
    old = await server.airiti_abstract("15601277-200010-201207250015-201207250015-49-72+171", include_cited_by=True)
    assert "## 被引用記錄" in old and "DocID：U" in old and "<a " not in old


async def test_search_shows_open_access():
    assert "[OpenAccess]" in await server.airiti_search("鷹架 數學解題", 1, 10)


async def test_thesis_style_docid_error_explains_why():
    with pytest.raises(Exception, match="airitietds.com"):
        await server.airiti_abstract("U0021-1610201315285936")


async def test_open_access_on_detail_page():
    out = await server.airiti_abstract("1027507x-201212-201301160049-201301160049-563-586")
    assert "開放取用：是" in out
