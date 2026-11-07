"""APA 7 (Chinese-style) reference built purely from the page's citation_* metadata.

Chinese journal articles only. Airiti writes the authors of an English article as "Given Family" (e.g.
"Hsiu-Ho Wang"); APA 7 needs "Family, G.-H.", and converting names automatically is error-prone (compound
surnames, hyphenated given names, romanised Chinese names), so such articles are refused with a pointer to the
RIS / BibTeX export, where the reference manager does the formatting.

Format rules, checked against four Taiwanese APA-7 guides (台灣護理學會 2022, 台灣護理教育學會, 國立臺東大學,
中華民國體育學會) on 2026-11-07:
  * 作者（年）。篇名。*期刊名*，*卷*（期），頁–頁。DOI.  Journal name and volume italic, the issue not.
  * No volume: "僅列期數且不需斜體，不需括號" -> *期刊名*，3，19–57。 (no parentheses, not italic).
  * Authors joined with 、; more than 20 -> the first 19, then 、……、, then the last author.
  * Not unanimous: the 體育學會 guide wants ALL brackets half-width with a space on each side; the other three print
    full-width （ ） in their Chinese examples, which is what we follow."""
from __future__ import annotations

import re

from .errors import AiritiError
from .parsers import Detail, is_thesis_detail

_CJK = re.compile(r"[\u3400-\u9fff]")


def apa7(d: Detail) -> str:
    missing = [n for n, v in (("作者", d.authors), ("出版年", d.date), ("期刊名", d.journal), ("篇名", d.title)) if not v]
    if missing:
        raise AiritiError(
            f"書目資料不足，無法產生 APA 引用（缺：{'、'.join(missing)}）。"
            "目前只支援有刊名的期刊論文；學位論文等請手動引用。"
        )
    if is_thesis_detail(d):
        raise AiritiError("這是學位論文（頁面的「刊名」欄是校系名稱，不是期刊），目前不產生 APA 引用，請手動引用。")
    ym = re.match(r"\s*(\d{4})", d.date)
    if not ym:
        raise AiritiError(f"無法從日期「{d.date}」解析出版年，不產生引用。")
    year = ym.group(1)

    if not (_CJK.search(d.title) and all(_CJK.search(a) for a in d.authors)):  # one Latin-named co-author is enough to refuse
        raise AiritiError(
            "目前只支援中文期刊論文（篇名與所有作者姓名皆為中文）的 APA 引用。這篇看起來是英文或外文論文：華藝給的作者是"
            "「名 姓」的全名，APA 要的是「姓, 名縮寫」，自動轉換很容易出錯，所以不產生。"
            "請改用 airiti_export_citation 匯出 RIS 或 BibTeX，到 EndNote／Zotero 讓軟體排版。"
        )
    names = d.authors
    authors = "、".join(names[:19]) + "、……、" + names[-1] if len(names) > 20 else "、".join(names)
    head = f"{authors}（{year}）。"
    vol = f"*{d.volume}*" if d.volume else ""
    iss = f"（{d.issue}）" if d.issue else ""
    pages = ""
    if d.first_page:
        same = not d.last_page or d.last_page == d.first_page  # a one-page piece is "15", not "15–15"
        pages = f"，{d.first_page}" if same else f"，{d.first_page}–{d.last_page}"
    if vol:
        vol_iss = f"，{vol}{iss}"
    elif d.issue:
        vol_iss = f"，{d.issue}"  # no volume: the bare issue number, plain, no parentheses
    else:
        vol_iss = ""
    title = d.title if d.title.rstrip()[-1:] in "？！。?!." else d.title + "。"  # no "？。" / "。。"
    body = f"{title}*{d.journal}*" + vol_iss + pages + "。"
    link = f"https://doi.org/{d.doi}" if d.doi else d.url
    return head + body + " " + link  # the space lets Markdown viewers turn the DOI / URL into a link
