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
        ITEM.format(key="15601277-49-72+171", click="15601277-49-72%252b171", n=1),
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
    assert apa7(parse_detail(DETAIL, "ID")) == "蔡欣倫、王小明（2024）。題目。*期刊名*，*1*（3），19–57。 https://doi.org/10.1/x(3).2"


def test_apa_issue_without_volume_is_the_bare_number_with_no_parentheses():
    # Taiwanese APA-7 guides: when the journal has no volume, list only the issue, not italic, "不需括號"
    d = parse_detail(DETAIL, "ID")
    d.volume = ""
    assert apa7(d) == "蔡欣倫、王小明（2024）。題目。*期刊名*，3，19–57。 https://doi.org/10.1/x(3).2"
    d.issue = ""
    assert "*期刊名*，19–57。" in apa7(d)  # neither volume nor issue
    d.first_page = ""
    assert "*期刊名*。" in apa7(d)  # and no pages either


def test_apa_lists_up_to_20_authors_and_elides_the_middle_beyond_that():
    d = parse_detail(DETAIL, "ID")
    d.authors = [f"作者{i}" for i in range(1, 21)]
    assert apa7(d).startswith("、".join(d.authors) + "（2024）")  # 20: all of them
    d.authors = [f"作者{i}" for i in range(1, 22)]
    head = apa7(d).split("（2024）")[0]
    assert head == "、".join(f"作者{i}" for i in range(1, 20)) + "、……、作者21"  # 21: first 19, ellipsis, last


def test_apa_refuses_articles_that_are_not_in_chinese():
    from airiti_mcp.errors import AiritiError

    d = parse_detail(DETAIL, "ID")
    d.authors = ["Hsiu-Ho Wang"]  # how Airiti writes English authors: "Given Family", not APA's "Family, G.-H."
    with pytest.raises(AiritiError, match="airiti_export_citation"):
        apa7(d)
    d = parse_detail(DETAIL, "ID")
    d.title = "An English title"  # Chinese author, English article
    with pytest.raises(AiritiError, match="中文期刊論文"):
        apa7(d)


def test_apa_refuses_when_data_is_missing_or_the_year_is_unreadable():
    from airiti_mcp.errors import AiritiError

    d = parse_detail(DETAIL, "ID")
    d.authors = []
    with pytest.raises(AiritiError, match="缺：作者"):
        apa7(d)
    d = parse_detail(DETAIL, "ID")
    d.date = "民國一一三年"
    with pytest.raises(AiritiError, match="無法從日期"):
        apa7(d)


def test_apa_refuses_a_thesis_by_its_docid_even_if_the_journal_field_looks_ordinary():
    from airiti_mcp.errors import AiritiError

    d = parse_detail(DETAIL, "U0001-1234")  # DocID of a thesis, journal field = a department name without 論文
    d.journal = "國立某大學化學研究所"
    with pytest.raises(AiritiError, match="學位論文"):
        apa7(d)


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


SHELL = "<html><title>Airiti Library</title></html>"  # what the site sends instead of a detail page


@pytest.fixture
def detail_site(monkeypatch):
    from airiti_mcp import harvest, server

    state = {"calls": [], "sleeps": [], "pages": []}

    async def fake_sleep(seconds):
        state["sleeps"].append(seconds)

    async def fetch(doc_id):
        state["calls"].append(doc_id)
        return state["pages"][min(len(state["calls"]) - 1, len(state["pages"]) - 1)]

    monkeypatch.setattr(harvest, "_sleep", fake_sleep)
    monkeypatch.setattr(server.client, "fetch_detail_html", fetch)
    return state


async def test_a_single_home_page_shell_is_retried_not_reported_as_an_invalid_docid(detail_site):
    from airiti_mcp import server

    detail_site["pages"] = [SHELL, DETAIL]  # the valid page comes back as a shell once
    out = await server.airiti_abstract("ID")
    assert "題目" in out and len(detail_site["calls"]) == 2 and detail_site["sleeps"] == [3.0]


async def test_a_docid_that_is_always_a_shell_is_reported_after_three_tries(detail_site):
    from mcp.server.mcpserver.exceptions import ToolError

    from airiti_mcp import server

    detail_site["pages"] = [SHELL]
    with pytest.raises(ToolError, match="請確認 DocID.*已重試 2 次"):
        await server.airiti_abstract("ID")
    assert len(detail_site["calls"]) == 3 and detail_site["sleeps"] == [3.0, 6.0]
    with pytest.raises(ToolError, match="U 開頭是學位論文"):  # the thesis hint still applies, and no longer claims they are unreadable
        await server.airiti_abstract("U0000-1")
    with pytest.raises(ToolError) as e:
        await server.airiti_cite_apa("ID")  # the APA tool shares the same retry
    assert "已重試 2 次" in str(e.value)


async def test_only_the_shell_is_retried(monkeypatch, detail_site):
    from airiti_mcp import harvest, server
    from airiti_mcp.errors import AiritiParseError

    def broken(html, doc_id, *a):  # a page that is a real detail page but cannot be understood
        raise AiritiParseError("structure changed")

    monkeypatch.setattr(harvest.parsers, "parse_detail", broken)
    detail_site["pages"] = [DETAIL]
    with pytest.raises(Exception, match="structure changed"):
        await server.airiti_abstract("ID")
    assert len(detail_site["calls"]) == 1 and detail_site["sleeps"] == []  # a redesign is not hammered three times


# ---------------- the issue number of journals without a volume: the page lies, the export tells the truth ----------------
NO_VOLUME = DETAIL.replace("name='citation_volume' content='1'", "name='citation_volume' content=''").replace(
    "name='citation_issue' content='3'", "name='citation_issue' content='42'"  # the page says 42 ...
)


def ris_with_issue(issue):
    import json

    body = "TY  - JOUR\r\nT1  - 題目\r\n" + (f"IS  - {issue}\r\n" if issue else "") + "PY  - 2006\r\nER  - \r\n\r\n"
    return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": body}})


@pytest.fixture
def export_site(detail_site, monkeypatch):
    """detail_site (fake detail pages, fake sleep) plus a fake CiteExport that records its calls."""
    from airiti_mcp import server

    detail_site["export_calls"] = []
    detail_site["export_body"] = ris_with_issue("642")  # ... the export says 642

    async def fake_export(ids, site_type):
        detail_site["export_calls"].append((list(ids), site_type))
        if isinstance(detail_site["export_body"], Exception):
            raise detail_site["export_body"]
        return detail_site["export_body"]

    monkeypatch.setattr(server.client, "fetch_cite_export", fake_export)
    return detail_site


def test_ris_issue_reads_the_first_record_only():
    from airiti_mcp.cite_export import ris_issue

    assert ris_issue("TY  - JOUR\r\nIS  - 642\r\nER  - \r\n") == "642"
    assert ris_issue("TY  - JOUR\r\nIS  - 101年度 \r\nER  - \r\n") == "101年度"
    assert ris_issue("TY  - JOUR\r\nT1  - no issue\r\nER  - \r\n\r\nTY  - JOUR\r\nIS  - 9\r\nER  - \r\n") == ""  # 2nd record is not ours
    assert ris_issue("") == ""


async def test_a_volume_less_article_gets_its_issue_from_the_export(export_site):
    from airiti_mcp import harvest

    export_site["pages"] = [NO_VOLUME]
    det = await harvest.fetch_detail("18166482-x")
    assert det.volume == "" and det.issue == "642"  # not the page's 42
    assert export_site["export_calls"] == [(["18166482-x"], "RIS")]  # one extra export, for this id only
    assert any("42" in n and "642" in n and "已採用匯出資料" in n for n in det.notes)  # and the user is told


async def test_the_corrected_issue_reaches_the_apa_reference(export_site):
    from airiti_mcp import server

    export_site["pages"] = [NO_VOLUME]
    ref = await server.airiti_cite_apa("ID")
    assert "*期刊名*，642，19–57。" in ref and "42，" not in ref.replace("642，", "")


async def test_an_article_with_a_volume_costs_no_extra_request(export_site):
    from airiti_mcp import harvest

    export_site["pages"] = [DETAIL]  # volume 1, issue 3: the page is reliable here
    det = await harvest.fetch_detail("ID")
    assert det.volume == "1" and det.issue == "3" and export_site["export_calls"] == []


async def test_matching_numbers_produce_no_note(export_site):
    from airiti_mcp import harvest

    export_site["pages"] = [NO_VOLUME.replace("content='42'", "content='642'")]
    det = await harvest.fetch_detail("ID")
    assert det.issue == "642" and not any("不一致" in n for n in det.notes)


async def test_a_page_issue_that_the_export_says_does_not_exist_is_dropped(export_site):
    from airiti_mcp import harvest

    export_site["export_body"] = ris_with_issue("")  # the export has no issue at all
    export_site["pages"] = [NO_VOLUME]
    det = await harvest.fetch_detail("ID")
    assert det.issue == "" and any("空白" in n for n in det.notes)


@pytest.mark.parametrize("failure", ["site refuses", "network down"])
async def test_if_the_export_cannot_be_reached_no_issue_is_printed_rather_than_a_wrong_one(export_site, failure):
    import json

    from airiti_mcp import harvest, server
    from airiti_mcp.errors import AiritiNetworkError

    export_site["export_body"] = (
        json.dumps({"ReturnValue": 1, "ReturnMessage": "處理失敗", "ReturnData": None}) if failure == "site refuses" else AiritiNetworkError("down")
    )
    export_site["pages"] = [NO_VOLUME]
    det = await harvest.fetch_detail("ID")
    assert det.issue == "" and any("所以不列期號" in n for n in det.notes)
    ref = await server.airiti_cite_apa("ID")
    assert "*期刊名*，19–57。" in ref  # journal and pages, but no (possibly wrong) issue number


def test_apa_refuses_a_mixed_author_list_and_a_missing_title():
    from airiti_mcp.errors import AiritiError

    d = parse_detail(DETAIL, "ID")
    d.authors = ["蔡欣倫", "John Smith"]  # one Latin-named co-author: we do not convert names, so no reference
    with pytest.raises(AiritiError, match="所有作者姓名皆為中文"):
        apa7(d)
    d = parse_detail(DETAIL, "ID")
    d.title = ""
    with pytest.raises(AiritiError, match="缺：篇名"):  # not the misleading "this is a foreign-language article"
        apa7(d)


def test_the_doi_is_separated_by_a_space_so_markdown_makes_it_a_link():
    assert "。 https://doi.org/" in apa7(parse_detail(DETAIL, "ID"))


def test_is_thesis_detail_uses_the_docid_and_the_journal_field():
    from airiti_mcp.parsers import is_thesis_detail

    d = parse_detail(DETAIL, "U0001-12")
    assert is_thesis_detail(d)  # by DocID
    d = parse_detail(DETAIL.replace("期刊名", "某大學資訊系學位論文"), "ID")
    assert is_thesis_detail(d)  # by the journal field
    assert not is_thesis_detail(parse_detail(DETAIL, "ID"))


@pytest.mark.parametrize("doc_id, journal", [("U0001-12", "期刊名"), ("ID", "某大學資訊系學位論文")], ids=["by-docid", "by-journal-field"])
async def test_a_thesis_costs_no_extra_export_request_and_gets_no_issue_note(export_site, doc_id, journal):
    from airiti_mcp import harvest

    export_site["pages"] = [NO_VOLUME.replace("期刊名", journal)]  # volume empty, like every thesis
    det = await harvest.fetch_detail(doc_id)
    assert export_site["export_calls"] == []  # a thesis has no issue to look up
    assert not any("期號" in n for n in det.notes)  # and no confusing "page vs export" note


def test_airitis_no_abstract_placeholder_is_not_presented_as_an_abstract():
    from airiti_mcp.parsers import NO_ABSTRACT_PLACEHOLDER

    assert NO_ABSTRACT_PLACEHOLDER == "文章暫無收錄摘要"
    head_only = DETAIL.split("<body>")[0] + "<body></body></html>"  # no abstract block in the page body
    # (1) only the meta tag carries the placeholder - what the real site does (4 of 20 pages)
    d = parse_detail(head_only.replace("content='被截斷的摘…'", "content='文章暫無收錄摘要'"), "ID")
    assert d.abstract == "" and not d.abstract_is_truncated
    assert any("沒有摘要" in n for n in d.notes) and not any("可能被截斷" in n for n in d.notes)
    # (2) the page body carries it while the meta has real (truncated) text: the placeholder is ignored, the meta is used
    d = parse_detail(DETAIL.replace("完整摘要。", "文章暫無收錄摘要"), "ID")
    assert d.abstract == "被截斷的摘…" and d.abstract_is_truncated
    # (3) both carry it
    both = DETAIL.replace("完整摘要。", "文章暫無收錄摘要").replace("content='被截斷的摘…'", "content='文章暫無收錄摘要'")
    assert parse_detail(both, "ID").abstract == ""
    # a real truncated meta abstract is still used as before
    assert parse_detail(head_only, "ID").abstract == "被截斷的摘…"
