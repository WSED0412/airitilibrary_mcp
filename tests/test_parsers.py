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


def test_apa_issue_without_volume():
    d = parse_detail(DETAIL, "ID")
    d.volume = ""
    assert "，（3），19–57。" in apa7(d)
    d.authors = ["Smith, J."]
    assert "*期刊名*, (3), 19–57." in apa7(d)


def test_cite_export_parsing():
    from airiti_mcp.cite_export import cite_export_error, parse_cite_export, parse_cite_token

    assert parse_cite_token("var ajaxRequestVerificationToken_CiteWindow = 'abc:def';") == "abc:def"
    with pytest.raises(AiritiParseError):
        parse_cite_token("<html></html>")
    ok = '{"ReturnValue":0,"ReturnData":{"ID":"x.bib","Content":"@article{a,}\\r\\n\\r\\n"}}'
    assert parse_cite_export(ok) == "@article{a,}"
    # site-reported failure / empty data -> None (caller retries), not an exception
    assert parse_cite_export('{"ReturnValue":1,"ReturnData":null}') is None
    assert cite_export_error('{"ReturnValue":1,"ReturnMessage":"處理失敗","ReturnData":null}') == "處理失敗"
    assert cite_export_error("not json") == ""
    with pytest.raises(AiritiParseError):
        parse_cite_export("<html>not json</html>")


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


def _with_refs(rows, label="顯示所有 2 篇參考文獻"):
    import json
    from urllib.parse import quote
    js = f"<script>var 全域_參考文獻 = '{quote(json.dumps(rows, ensure_ascii=False))}';</script>"
    block = f'<div id="Detail_參考文獻_錨點">{label}</div>'
    return DETAIL.replace("</body>", block + js + "</body>")


REFS = [
    {"Seq": "1", "Reference": "甲（2011）。 論文A。", "RDocID": "U0030-X", "RDOI": "10.1/a"},
    {"Seq": "2", "Reference": "乙 (2012).  Paper B.", "RDocID": None, "RDOI": None},
]


def test_references_off_by_default():
    assert parse_detail(_with_refs(REFS), "ID").references is None


def test_references_parsed_and_count_checked():
    d = parse_detail(_with_refs(REFS), "ID", with_references=True)
    assert [(r.text, r.doc_id, r.doi) for r in d.references] == [
        ("甲（2011）。 論文A。", "U0030-X", "10.1/a"), ("乙 (2012). Paper B.", "", "")]
    assert not any("不一致" in n for n in d.notes)


def test_references_count_mismatch_is_reported():
    d = parse_detail(_with_refs(REFS, "顯示所有 5 篇參考文獻"), "ID", with_references=True)
    assert len(d.references) == 2 and any("不一致" in n and "5" in n for n in d.notes)


def test_references_missing_variable_is_stated_not_silent():
    d = parse_detail(DETAIL, "ID", with_references=True)
    assert d.references == [] and any("參考文獻" in n for n in d.notes)


def test_references_corrupt_json_is_loud():
    html = DETAIL.replace("</body>", "<script>var 全域_參考文獻 = '%7bbroken';</script></body>")
    with pytest.raises(AiritiParseError):
        parse_detail(html, "ID", with_references=True)


def test_search_single_result_does_not_climb_to_whole_page():
    sidebar = '<div class="side"><span class="author"><a>無關作者</a></span><span class="source"><span class="sourceTitleName">無關刊</span></span></div>'
    r = parse_search(search_html("1 個查詢結果", [ITEM.format(key="K1", click="K1", n=1)]).replace("</div>", sidebar + "</div>", 1))
    assert [i.doc_id for i in r.items] == ["K1"]
    assert r.items[0].authors == ["甲(A)", "乙"] and "無關" not in r.items[0].source


def test_search_count_needs_the_unit_word():
    with pytest.raises(AiritiParseError):
        parse_search('<div class="resultReport">第 1 頁</div>')
    assert parse_search(search_html("1,234 個查詢結果", [ITEM.format(key="K", click="K", n=1)])).total == 1234


def test_keywords_english_only_record():
    html = DETAIL.replace("甲;乙｜alpha;beta", "alpha;beta")
    d = parse_detail(html, "ID")
    assert d.keywords_zh == [] and d.keywords_en == ["alpha", "beta"]


@pytest.mark.parametrize("name", ["某大學資訊系碩士論文", "某大學教育學系博士論文", "某大學學位論文"])
def test_apa_refuses_thesis_variants(name):
    with pytest.raises(AiritiError):
        apa7(parse_detail(DETAIL.replace("期刊名", name), "ID"))


def _extras_html(cited_rows=None):
    import json
    from urllib.parse import quote
    html = DETAIL.replace("<meta name='citation_title'", "<meta name='citation_language' content='en;zh-tw' /><meta name='citation_title'")
    extra = "<ul><li class='preTag journal'><span>期刊</span></li><li class='preTag'><span>OpenAccess</span></li></ul>"
    extra += "<div class='downloadPoint 可全文下載'>x</div>"
    extra += "<script>var 全域_編碼次要篇名 = '" + quote("English Title") + "';</script>"
    if cited_rows is not None:
        extra += "<script>var 全域_被引用記錄 = '" + quote(json.dumps(cited_rows, ensure_ascii=False)) + "';</script>"
    return html.replace("</body>", extra + "</body>")


def test_detail_extra_fields():
    d = parse_detail(_extras_html(), "ID")
    assert d.title_parallel == "English Title" and d.language == "en;zh-tw"
    assert d.open_access and d.fulltext_marker
    assert d.cited_by is None  # opt-in


def test_detail_extra_fields_absent_are_empty_not_guessed():
    d = parse_detail(DETAIL, "ID")
    assert (d.title_parallel, d.language, d.open_access, d.fulltext_marker) == ("", "", False, False)


def test_cited_by_strips_html_and_keeps_docid():
    rows = [{"Reference": "李文雅（2017）。<a href='https://x/y'>某論文</a>&amp;X", "RDocID": "U0017-1", "RDOI": "10.1/z"}]
    d = parse_detail(_extras_html(rows), "ID", with_cited_by=True)
    assert [(r.text, r.doc_id, r.doi) for r in d.cited_by] == [("李文雅（2017）。某論文&X", "U0017-1", "10.1/z")]


def test_cited_by_zero_and_missing_are_stated():
    d0 = parse_detail(_extras_html([]), "ID", with_cited_by=True)
    assert d0.cited_by == [] and any("被引用 0 筆" in n for n in d0.notes)
    dm = parse_detail(DETAIL, "ID", with_cited_by=True)
    assert dm.cited_by == [] and any("被引用記錄" in n for n in dm.notes)


def test_search_open_access_tag():
    item = ITEM.format(key="K", click="K", n=1).replace("</ul>", "<li class='preTag'><span>OpenAccess</span></li></ul>")
    r = parse_search(search_html(items=[item]))
    assert r.items[0].open_access and r.items[0].kind == "期刊"


def test_empty_string_js_variable_means_none_not_a_parse_error():
    html = DETAIL.replace("</body>", "<script>var 全域_參考文獻 = '';var 全域_被引用記錄 = '';</script></body>")
    d = parse_detail(html, "ID", with_references=True, with_cited_by=True)
    assert d.references == [] and d.cited_by == []


def test_search_item_without_own_checkbox_is_loud_not_misattributed():
    ok = ITEM.format(key="K1", click="K1", n=1)
    broken = ITEM.format(key="K2", click="K2", n=2).replace('<input type="checkbox" class="Result_選擇" key="K2" key1="P001">', "")
    with pytest.raises(AiritiParseError):
        parse_search(search_html(items=[ok, broken]))
