"""Files written by airiti_search_all: a result list (CSV) and a search log for the methods section.

Pure functions (no network) so they can be tested offline."""
from __future__ import annotations

import csv
import io
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .parsers import SearchItem

DEFAULT_DIR_NAME = "airiti_exports"
ENV_DIR = "AIRITI_EXPORT_DIR"


def export_root() -> Path:
    """Where result folders are created. Fixed default in the home directory; the user can
    change it with the AIRITI_EXPORT_DIR environment variable (tools never take a path)."""
    raw = (os.environ.get(ENV_DIR) or "").strip().strip('"').strip("'").strip()  # cmd's set VAR="x" keeps the quotes
    return Path(raw).expanduser() if raw else Path.home() / DEFAULT_DIR_NAME


def run_folder_name(query: str, when: datetime) -> str:
    slug = re.sub(r"[^\w]+", "_", query).strip("_")[:30] or "query"
    return f"{when:%Y%m%d_%H%M%S}_{slug}"


def build_csv(items: list[SearchItem]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["序號", "DocID", "篇名", "作者", "出處", "類型", "OpenAccess", "網址"])
    for i, it in enumerate(items, 1):
        w.writerow([
            i, it.doc_id, it.title, "；".join(it.authors), it.source, it.kind,
            "Y" if it.open_access else "", it.url,
        ])
    return buf.getvalue()


def build_log(
    *, query: str, when: datetime, reported_total: int, fetched: int, unique: int,
    pages_fetched: int, page_size: int, cap: int, items: list[SearchItem], error: str = "",
    export_lines: list[str] | None = None, deduped: int | None = None,
) -> str:
    deduped = unique if deduped is None else deduped
    truncated = reported_total > cap
    kinds = Counter(it.kind or "（未標示）" for it in items)
    lines = [
        "華藝線上圖書館（Airiti Library）檢索紀錄",
        "=" * 40,
        f"檢索日期時間：{when:%Y-%m-%d %H:%M:%S %z}",
        "資料庫：華藝線上圖書館 Airiti Library（https://www.airitilibrary.com）",
        f"檢索式（原樣）：{query}",
        "檢索欄位：所有欄位（含全文）",
        "邏輯：以空白分隔的每個詞皆須符合（AND）；未使用 OR / NOT",
        "篩選條件：無（未限制年份、文獻類型、期刊）",
        "",
        f"華藝回報總筆數：{reported_total}",
        f"本次實際取得：{fetched} 筆（每頁 {page_size} 筆，共抓 {pages_fetched} 頁）",
        f"去除重複 DocID：移除 {fetched - deduped} 筆，剩 {deduped} 筆",
        f"超過上限而未納入：{deduped - unique} 筆（不是重複）" if deduped > unique else "超過上限而未納入：0 筆",
        f"納入清單（results.csv）：{unique} 筆",
        "取得筆數上限：" + f"{cap}" + ("（總筆數超過上限，結果被截斷，不是完整結果）" if truncated else "（未觸及上限）"),
        "類型分布：" + "；".join(f"{k} {n}" for k, n in kinds.most_common()),
    ]
    if export_lines:
        lines += [""] + export_lines
    if error:
        lines += ["", f"【不完整】抓取中途失敗：{error}"]
    lines += [
        "",
        "備註：",
        "- 學位論文（DocID 以 U 開頭）已分流至 airitietds.com，無法用本工具讀取詳目或匯出書目。",
        "- 本紀錄只描述這一次檢索；去重、篇名摘要篩選等後續步驟需另行記錄。",
    ]
    return "\n".join(lines) + "\n"
