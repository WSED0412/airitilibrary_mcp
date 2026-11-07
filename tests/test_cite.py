"""cite.apa7 details found in review: one-page articles and titles that already end in punctuation."""
from test_parsers import DETAIL

from airiti_mcp.cite import apa7
from airiti_mcp.parsers import parse_detail


def test_a_one_page_article_is_cited_with_a_single_page_number():
    d = parse_detail(DETAIL.replace("content='57'", "content='19'"), "ID")
    assert "，*1*（3），19。" in apa7(d) and "19–19" not in apa7(d)


def test_a_title_that_already_ends_in_punctuation_gets_no_second_full_stop():
    for end in "？！。":
        d = parse_detail(DETAIL.replace("content='題目'", f"content='落實了嗎{end}'"), "ID")
        out = apa7(d)
        assert f"落實了嗎{end}*期刊名*" in out and f"{end}。" not in out
    d = parse_detail(DETAIL, "ID")
    assert "。題目。*期刊名*" in apa7(d)  # an ordinary title still gets its full stop
