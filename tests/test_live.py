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
    assert "0 筆" in await server.airiti_search("zzzxqjkw不存在的詞")


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
