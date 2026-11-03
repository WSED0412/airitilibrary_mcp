from airiti_mcp.hints import FEW_RESULTS, drop_one_queries, few_results_hint


def test_drop_one_keyword():
    assert drop_one_queries("客語沉浸式教學 幼兒園") == ["幼兒園", "客語沉浸式教學"]
    assert drop_one_queries("a b c") == ["b c", "a c", "a b"]


def test_single_keyword_has_no_drop_suggestion():
    assert drop_one_queries("鷹架") == [] and drop_one_queries("") == []


def test_hint_text_does_not_claim_variant_chars_matter():
    h = few_results_hint("客語沉浸式教學 幼兒園", 3)
    assert "只找到 3 筆" in h and "「幼兒園」" in h and "AND" in h
    assert "沈" not in h and FEW_RESULTS == 5
