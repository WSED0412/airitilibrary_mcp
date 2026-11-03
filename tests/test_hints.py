from airiti_mcp.hints import FEW_RESULTS, drop_one_queries, few_results_hint, variant_queries


def test_drop_one_keyword():
    assert drop_one_queries("客語沉浸式教學 幼兒園") == ["幼兒園", "客語沉浸式教學"]
    assert drop_one_queries("a b c") == ["b c", "a c", "a b"]
    assert drop_one_queries("鷹架") == [] and drop_one_queries("") == []


def test_variants_only_for_pairs_the_site_does_not_unify():
    assert variant_queries("啟發 教學") == ["啓發 教學"]
    assert variant_queries("裡面") == ["裏面"] and variant_queries("污染") == ["汙染"]
    # verified live to be treated as equal by Airiti -> no suggestion
    for q in ("沉浸", "沈浸", "台灣", "群體", "溫度", "週期", "峰值"):
        assert variant_queries(q) == [], q


def test_hint_text():
    h = few_results_hint("啟發 幼兒園", 3)
    assert "只找到 3 筆" in h and "「啓發 幼兒園」" in h and "「幼兒園」" in h and "AND" in h
    assert "沒有統一" not in few_results_hint("客語沉浸式 幼兒園", 3)
    assert FEW_RESULTS == 5
