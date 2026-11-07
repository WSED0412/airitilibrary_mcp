"""search_export.py and the pure helpers around it: CSV, search log, duplicate flags, folder names."""
import csv
import io


from airiti_mcp.search_export import build_csv, build_log, duplicate_groups, is_preprint, run_folder_name
from helpers import _it, _item


def test_log_counts_kinds_and_flags_truncation():
    from datetime import datetime

    items = [_item(1), _item(2, "學位論文"), _item(3)]
    log = build_log(
        query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=500, fetched=3, unique=3,
        pages_fetched=1, page_size=20, cap=3, items=items,
    )
    assert "期刊 2" in log and "學位論文 1" in log and "結果被截斷" in log
    assert build_csv([]).startswith("序號,DocID")


def test_run_folder_name_is_filesystem_safe():
    from datetime import datetime

    name = run_folder_name('鷹架: 數學/解題?*"', datetime(2026, 11, 6, 15, 0, 0))
    assert name == "20261106_150000_鷹架_數學_解題"
    assert run_folder_name("???", datetime(2026, 11, 6, 15, 0, 0)).endswith("_query")


def test_export_dir_env_tolerates_quotes_and_tilde(tmp_path, monkeypatch):
    from airiti_mcp.search_export import export_root

    target = tmp_path / "My Exports"
    monkeypatch.setenv("AIRITI_EXPORT_DIR", '"' + str(target) + '"')
    assert export_root() == target
    export_root().mkdir(parents=True)  # no WinError 123
    monkeypatch.setenv("AIRITI_EXPORT_DIR", "")
    assert export_root().name == "airiti_exports"


def test_duplicate_groups_flags_same_title_and_authors_only():
    a = ["黃甲(Jia Huang)", "邱乙(Yi Chiou)"]
    items = [
        _it("P1", "以鷹架融入教學：個案研究", a),
        _it("P2", "以鷹架融入教學: 個案研究", ["黃甲", "邱乙(Y. Chiou)"]),  # punctuation / romanisation differ
        _it("P3", "以鷹架融入教學：個案研究", ["王丙"]),  # same title, different author -> a different paper
        _it("P4", "完全不同的篇名", a),
        _it("P5", "Scaffolding in Class", ["Lee"]),
        _it("P6", "SCAFFOLDING  in class", ["lee"]),  # case / spacing
        _it("P7", "", ["x"]),
        _it("P8", "", ["x"]),  # empty titles are never grouped
    ]
    g = duplicate_groups(items)
    assert g == {"P1": "D1", "P2": "D1", "P5": "D2", "P6": "D2"}


def test_csv_marks_preprint_and_duplicate_group_without_dropping_rows():
    final = _it("P1", "同一篇", ["甲"], "《期刊》 12卷1期 (2025/04) Pp. 39-70")
    pre = _it("P2", "同一篇", ["甲"], "《期刊》 預刊文章 Pp. 1-32")
    other = _it("P3", "別篇", ["乙"])
    assert not is_preprint(final) and is_preprint(pre)
    rows = list(csv.reader(io.StringIO(build_csv([final, pre, other]), newline="")))
    assert rows[0][-2:] == ["預刊", "疑似重複組"] and len(rows) == 4  # nothing removed
    assert [r[-2:] for r in rows[1:]] == [["", "D1"], ["Y", "D1"], ["", ""]]


def test_log_reports_duplicate_groups_and_preprints():
    from datetime import datetime

    items = [_it("P1", "同一篇", ["甲"]), _it("P2", "同一篇", ["甲"], "《期刊》 預刊文章 Pp. 1-32"), _it("P3", "別篇", ["乙"])]
    log = build_log(query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=3, fetched=3, unique=3,
                    pages_fetched=1, page_size=20, cap=100, items=items)
    assert "未自動移除）：1 組、涉及 2 筆" in log and "預刊文章" in log and "：1 筆，見 results.csv「預刊」欄" in log
    clean = build_log(query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=1, fetched=1, unique=1,
                      pages_fetched=1, page_size=20, cap=100, items=[_it("P3", "別篇", ["乙"])])
    assert "：0 組、涉及 0 筆" in clean


def test_duplicate_groups_ignores_records_without_authors_and_folds_full_width():
    # editorials in different journals: same title, no author -> not one paper
    assert duplicate_groups([_it("1", "編者的話", [], "A期刊 2010"), _it("2", "編者的話", [], "B期刊 2024")]) == {}
    # full-width vs ASCII letters/digits are the same title
    g = duplicate_groups([_it("1", "5G通訊技術之應用", ["甲"]), _it("2", "５Ｇ通訊技術之應用", ["甲"], "預刊文章")])
    assert g == {"1": "D1", "2": "D1"}


def test_log_flags_more_records_than_the_site_reported():
    from datetime import datetime

    log = build_log(query="q", when=datetime(2026, 11, 6, 15, 0, 0).astimezone(), reported_total=40, fetched=43, deduped=43,
                    unique=43, pages_fetched=3, page_size=20, cap=100, items=[_it(str(i), "t" + str(i), ["a"]) for i in range(43)], expected=40)
    assert "取得不重複 43 筆 → 多 3 筆（站方的資料可能在抓取期間更新）" in log
