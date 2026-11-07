"""Files written by airiti_search_all: a result list (CSV) and a search log for the methods section.

Pure functions (no network) so they can be tested offline."""
from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
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


def is_preprint(it: SearchItem) -> bool:
    """Airiti lists an article twice once it is published: the advance-online ("預刊文章") record and
    the issue record. The advance-online one says so in its source line."""
    return "預刊" in (it.source or "")


def _norm_title(title: str) -> str:
    # NFKC folds full-width letters/digits ("５Ｇ" -> "5G"), which print layouts switch between freely
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", title or "").casefold())


def _norm_author(name: str) -> str:
    """'蔡欣倫(Hsin-Luen Tsai)' -> '蔡欣倫': compare the name as printed first, ignore the romanisation."""
    return re.split(r"[(（]", name or "", maxsplit=1)[0].strip().casefold()


def duplicate_groups(items: list[SearchItem]) -> dict[str, str]:
    """doc_id -> group label ("D1", "D2", ...) for records that share normalised title AND author set.
    Only flags; nothing is removed (which record to keep is the reviewer's call). Records with the same
    title but different authors are different papers, and records without any author are never grouped
    ("編者的話" in two journals is not one article)."""
    buckets: dict[tuple[str, frozenset[str]], list[str]] = {}
    for it in items:
        key = (_norm_title(it.title), frozenset(a for a in map(_norm_author, it.authors) if a))
        if key[0] and key[1]:  # no author -> editorials, tables of contents...: same title is not same paper
            buckets.setdefault(key, []).append(it.doc_id)
    out: dict[str, str] = {}
    n = 0
    for ids in buckets.values():
        if len(ids) > 1:
            n += 1
            out.update({i: f"D{n}" for i in ids})
    return out


def make_export_folder(label: str, when: datetime) -> Path:
    """A fresh folder under the export root for a one-shot export (never reuses or overwrites one)."""
    root = export_root()
    root.mkdir(parents=True, exist_ok=True)
    base = f"{when:%Y%m%d_%H%M%S}_{label}"
    for i in range(1, 100):
        folder = root / (base if i == 1 else f"{base}_{i}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise OSError(f"cannot create a fresh export folder under {root}")


def build_csv(items: list[SearchItem]) -> str:
    groups = duplicate_groups(items)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["序號", "DocID", "篇名", "作者", "出處", "類型", "OpenAccess", "網址", "預刊", "疑似重複組"])
    for i, it in enumerate(items, 1):
        w.writerow([
            i, it.doc_id, it.title, "；".join(it.authors), it.source, it.kind,
            "Y" if it.open_access else "", it.url, "Y" if is_preprint(it) else "", groups.get(it.doc_id, ""),
        ])
    return buf.getvalue()


def build_log(
    *, query: str, when: datetime, reported_total: int, fetched: int, unique: int,
    pages_fetched: int, page_size: int, cap: int, items: list[SearchItem], error: str = "",
    export_lines: list[str] | None = None, deduped: int | None = None, run_note: str = "",
    expected: int | None = None, page_retries: int = 0, notes: list[str] | None = None, repaired: int = 0,
) -> str:
    deduped = unique if deduped is None else deduped
    truncated = reported_total > cap
    kinds = Counter(it.kind or "（未標示）" for it in items)
    groups = duplicate_groups(items)
    n_groups = len(set(groups.values()))
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
        f"去除重複 DocID：移除 {fetched + repaired - deduped} 筆，剩 {deduped} 筆"
        + (f"（含邊界修補補回的 {repaired} 筆在內共 {fetched + repaired} 列）" if repaired else ""),
        f"超過上限而未納入：{deduped - unique} 筆（不是重複）" if deduped > unique else "超過上限而未納入：0 筆",
        f"納入清單（results.csv）：{unique} 筆",
        f"疑似重複（篇名與作者相同、DocID 不同；未自動移除）：{n_groups} 組、涉及 {len(groups)} 筆，見 results.csv「疑似重複組」欄",
        f"預刊文章（出處標示預刊，通常與正式刊出版本並存）：{sum(is_preprint(it) for it in items)} 筆，見 results.csv「預刊」欄",
        "取得筆數上限：" + f"{cap}" + ("（總筆數超過上限，結果被截斷，不是完整結果）" if truncated else "（未觸及上限）"),
        "類型分布：" + "；".join(f"{k} {n}" for k, n in kinds.most_common()),
    ]
    extra: list[str] = []
    if expected is not None:
        missing = max(0, expected - deduped)
        line = f"完整性核對：預期 {expected} 筆，取得不重複 {deduped} 筆"
        if deduped > expected:
            line += f" → 多 {deduped - expected} 筆（站方的資料可能在抓取期間更新）"
        elif not missing:
            line += " → 完整"
        elif error:
            line += f" → 缺 {missing} 筆（抓取中途放棄，見下方【不完整】）"
        else:
            line += (
                f" → 缺 {missing} 筆。可能原因：頁邊界上排名相同的記錄在兩次請求之間互換位置，造成一筆重複、一筆漏抓；"
                "建議縮小查詢範圍，或再跑一次互相比對。"
            )
        if expected < reported_total:
            line += f"（只核對所抓頁數範圍內的 {expected} 筆；總筆數 {reported_total}、上限 {cap}）"
        extra.append(line)
    if page_retries:
        extra.append(f"頁面重試：{page_retries} 次（華藝偶爾對有效頁碼暫時回 0 筆或出錯，已自動重試補回）")
    extra += notes or []
    at = next(i for i, l in enumerate(lines) if l.startswith("納入清單"))
    lines[at + 1 : at + 1] = extra
    if run_note:
        lines.append(run_note)
    if export_lines:
        lines += [""] + export_lines
    if error:
        lines += ["", f"【不完整】抓取中途失敗：{error}"]
    lines += [
        "",
        "備註：",
        "- 學位論文（DocID 以 U 開頭、類型欄為「學位論文」）可讀取詳目並匯出書目；APA 引用目前只支援期刊論文。",
        "- 疑似重複只依「篇名＋作者」判斷，可能漏掉篇名改過的版本，也不保證每組都是同一篇；要保留哪一筆請自行判斷（通常保留正式刊出版本）。",
        "- 本紀錄只描述這一次檢索；篇名摘要篩選等後續步驟需另行記錄。",
    ]
    return "\n".join(lines) + "\n"
