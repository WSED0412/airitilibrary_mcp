from airiti_mcp.hints import FEW_RESULTS, few_results_hint, variant_queries


def test_variant_swaps_one_char_everywhere():
    assert variant_queries("客語沉浸式教學 幼兒園") == ["客語沈浸式教學 幼兒園"]
    assert variant_queries("台灣 台北") == ["臺灣 臺北"]
    assert variant_queries("客語沈浸") == ["客語沉浸"]


def test_variant_multiple_chars_one_suggestion_each():
    assert set(variant_queries("台灣沉浸")) == {"臺灣沉浸", "台灣沈浸"}


def test_no_variants_no_suggestions():
    assert variant_queries("鷹架 數學解題") == []
    assert variant_queries("") == []


def test_hint_text():
    h = few_results_hint("客語沉浸式教學", 3)
    assert "只找到 3 筆" in h and "「客語沈浸式教學」" in h
    assert "異體字寫法" not in few_results_hint("鷹架", 0)  # nothing concrete to suggest -> no variant line
    assert FEW_RESULTS == 5
