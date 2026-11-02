import pytest

from airiti_mcp.cite import apa7
from airiti_mcp.client import build_query_string, detail_url
from airiti_mcp.errors import AiritiError, AiritiParseError
from airiti_mcp.parsers import parse_detail, parse_search

ITEM = """
<div class="item"><div class="grid_data"><label>
<input type="checkbox" class="Result_選擇" key="{key}" key1="P001"></label>
<ul><li class="preTag journal"><span class="listType"></span><span>期刊</span></li></ul></div>
<div class="grid_data"><h3 class="ustyle_heading_H3">
<a href="javascript:;" onclick="_文章書目_點擊篇名('{click}', 'X', 'q', 'false', '', 'P001');">篇<span>名</span>{n}</a></h3></div>
<div class="grid_data"><span class="author"><span><a onclick="x('a','b');">甲(A)</a></span><span><a>乙</a></span></span></div>
<div class="grid_data"><span class="source"><a><span class="sourceTitleName">《某期刊》</span>
<span class="sourcePub">1卷3期</span><span class="sourcedate">(2024 / 09)</span><span class="sourcePageRange">Pp. 19-57</span></a></span></div>
</div>"""


def search_html(total="282 個查詢結果", items=()):
    return f'<div class="resultReport">{total}</div><div class="list">{"".join(items)}</div>'


def test_search_parses_items_and_prefers_checkbox_docid():
    html = search_html(items=[
        ITEM.format(key="15601277-49-72+171", click="15601277-49-72%2b171", n=1),
        ITEM.format(key="U0021-1610", click="U0021-1610", n=2),
    ])
    r = parse_search(html)
    assert r.total == 282
    assert [i.doc_id for i in r.items] == ["15601277-49-72+171", "U0021-1610"]
    assert r.items[0].title.replace(" ", "") == "篇名1"
    assert r.items[0].authors == ["甲(A)", "乙"]
    assert "某期刊" in r.items[0].source and "1卷3期" in r.items[0].source
    assert r.items[0].kind == "期刊"
    assert "%2B" in r.items[0].url  # "+" is encoded exactly once


def test_search_zero_results_is_not_a_parse_error():
    r = parse_search(search_html(total="0 個查詢結果"))
    assert r.total == 0 and r.items == []


def test_search_zero_results_real_site_block():
    html = '<div class="searchNoResult"><script>var x=1;</script> 很抱歉，您輸入之查詢條件的查詢筆數為 0 筆。</div>'
    assert parse_search(html).total == 0


def test_search_structure_change_is_loud():
    with pytest.raises(AiritiParseError):
        parse_search("<html><body>captcha?</body></html>")
    with pytest.raises(AiritiParseError):  # count but no parsable items
        parse_search(search_html(total="5 個查詢結果"))


DETAIL = """<html><head>
<meta name='citation_title' content='題目' /><meta name='citation_author' content='蔡欣倫' />
<meta name='citation_author' content='王小明' />
<meta name='citation_journal_title' content="期刊名"><meta name='citation_volume' content='1' />
<meta name='citation_issue' content='3' /><meta name='citation_firstpage' content='19' />
<meta name='citation_lastpage' content='57' /><meta name='citation_date' content='2024/09/10' />
<meta name='citation_doi' content='10.1/x(3).2' />
<meta name='citation_abstract' content='被截斷的摘…' />
<meta name='citation_keywords' content='甲;乙｜alpha;beta' /></head><body>
<div class="academicAbstractSet"><h3><span>摘要</span></h3><div class="academicAbstractContent">
<div class="academicAbstractGroup"><p>完整摘要。</p></div></div></div>
<div class="academicAlignAbstractSet"><h3><span>並列摘要</span></h3><div class="academicAlignAbstractContent">
<div class="academicAbstractGroup"><p>Full abstract.</p></div></div></div></body></html>"""


def test_detail_full():
    d = parse_detail(DETAIL, "ID")
    assert d.title == "題目" and d.authors == ["蔡欣倫", "王小明"]
    assert d.abstract == "完整摘要。" and not d.abstract_is_truncated
    assert d.abstract_parallel == "Full abstract."
    assert d.keywords_zh == ["甲", "乙"] and d.keywords_en == ["alpha", "beta"]
    assert d.doi == "10.1/x(3).2"


def test_detail_english_headings_fallback():
    html = DETAIL.replace('class="academicAbstractSet"', "").replace('class="academicAlignAbstractSet"', "")
    html = html.replace("<span>摘要</span>", "<span>Abstracts</span>").replace("<span>並列摘要</span>", "<span>Parallel abstracts</span>")
    d = parse_detail(html, "ID")
    assert d.abstract == "完整摘要。" and d.abstract_parallel == "Full abstract."


def test_detail_missing_abstract_falls_back_to_meta_and_says_so():
    html = DETAIL.split("<body>")[0] + "<body></body></html>"
    d = parse_detail(html, "ID")
    assert d.abstract == "被截斷的摘…" and d.abstract_is_truncated
    assert any("截斷" in n for n in d.notes)


def test_detail_not_an_article_page():
    with pytest.raises(AiritiParseError):
        parse_detail("<html><title>Airiti Library</title></html>", "NOPE")


def test_apa_chinese():
    assert apa7(parse_detail(DETAIL, "ID")) == "蔡欣倫、王小明（2024）。題目。*期刊名*，*1*（3），19–57。https://doi.org/10.1/x(3).2"


def test_apa_refuses_thesis():
    d = parse_detail(DETAIL.replace("期刊名", "某大學資訊系學位論文"), "ID")
    with pytest.raises(AiritiError):
        apa7(d)


def test_query_string_shape():
    import json
    from urllib.parse import unquote
    q = json.loads(unquote(build_query_string("鷹架 數學解題", 2, 20)))
    assert q["DSF"]["SearchFileds"][0]["FieldName"] == 49 and q["DSF"]["Page"] == 2 and q["DSF"]["PageSize"] == 20
    assert [f["SearchKeyWord"] for f in q["BSF"]["SearchFiledList"]] == ["鷹架", "數學解題"]


def test_detail_url_encodes_once():
    assert detail_url("a+b/c") == "https://www.airitilibrary.com/Article/Detail?DocID=a%2Bb%2Fc"
