"""APA 7 (Chinese-style) reference built purely from the page's citation_* metadata."""
from __future__ import annotations

import re

from .errors import AiritiError
from .parsers import Detail

_CJK = re.compile(r"[\u3400-\u9fff]")


def apa7(d: Detail) -> str:
    missing = [n for n, v in (("作者", d.authors), ("出版年", d.date), ("期刊名", d.journal)) if not v]
    if missing:
        raise AiritiError(
            f"書目資料不足，無法產生 APA 引用（缺：{'、'.join(missing)}）。"
            "目前只支援有刊名的期刊論文；學位論文等請手動引用。"
        )
    if d.journal.endswith("學位論文"):
        raise AiritiError("這是學位論文（頁面的「刊名」欄是校系名稱，不是期刊），目前不產生 APA 引用，請手動引用。")
    ym = re.match(r"\s*(\d{4})", d.date)
    if not ym:
        raise AiritiError(f"無法從日期「{d.date}」解析出版年，不產生引用。")
    year = ym.group(1)

    zh = bool(_CJK.search(d.authors[0]))
    if zh:
        authors = "、".join(d.authors)
        head = f"{authors}（{year}）。"
        vol = f"*{d.volume}*" if d.volume else ""
        iss = f"（{d.issue}）" if d.issue else ""
        pages = ""
        if d.first_page:
            pages = f"，{d.first_page}–{d.last_page}" if d.last_page else f"，{d.first_page}"
        body = f"{d.title}。*{d.journal}*" + (f"，{vol}{iss}" if vol else "") + pages + "。"
    else:
        authors = ", ".join(d.authors)
        head = f"{authors} ({year}). "
        vol = f", *{d.volume}*" if d.volume else ""
        iss = f"({d.issue})" if d.issue else ""
        pages = f", {d.first_page}–{d.last_page}" if d.first_page and d.last_page else (f", {d.first_page}" if d.first_page else "")
        body = f"{d.title}. *{d.journal}*{vol}{iss}{pages}."
    link = f"https://doi.org/{d.doi}" if d.doi else d.url
    return head + body + ("" if zh else " ") + link
