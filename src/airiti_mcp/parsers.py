"""HTML parsers for the search-result page and the article detail page.

Parsing is structural (CSS classes / meta tags verified against live pages) with heading-text
fallbacks for the abstract blocks (zh: 摘要/關鍵字/並列摘要/並列關鍵字, en: Abstracts/
Keywords/Parallel abstracts/Parallel keywords). DocIDs are never matched against a
fixed format: they are lifted from the result item's own onclick/attribute.
"""
from __future__ import annotations

import json
import re
from html import unescape
from dataclasses import dataclass, field
from urllib.parse import unquote

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .client import detail_url
from .errors import AiritiParseError, AiritiShellPageError

_ONCLICK_DOCID = re.compile(r"\(\s*'([^']+)'")
_COUNT = re.compile(r"([\d,]+)\s*個查詢結果")


def _text(node: LexborNode | None) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip() if node else ""


# ---------------------------------------------------------------- search ----
@dataclass
class SearchItem:
    doc_id: str
    title: str
    authors: list[str]
    source: str  # 刊名 / 卷期 / 年月 / 頁碼, as shown on the site
    kind: str  # 期刊 / 學位論文 / ...
    url: str
    open_access: bool = False  # 'OpenAccess' tag shown on the result


@dataclass
class SearchPage:
    total: int
    items: list[SearchItem] = field(default_factory=list)


def _ancestors(node: LexborNode):
    while node.parent is not None:
        node = node.parent
        yield node


def parse_search(html: str) -> SearchPage:
    tree = LexborHTMLParser(html)
    report = tree.css_first("div.resultReport")
    if report is None:
        # The site renders a dedicated block for an empty result ("…查詢筆數為 0 筆。"). Require it
        # explicitly so that a changed/blocked page is never mistaken for "zero results".
        empty = tree.css_first("div.searchNoResult")
        if empty is not None:
            for s in empty.css("script"):
                s.decompose()
            if re.search(r"為\s*0\s*筆", _text(empty)):
                return SearchPage(total=0)
        raise AiritiParseError(
            "搜尋結果頁找不到總筆數區塊（div.resultReport）。可能是華藝改版、被導向到其他頁面，或查詢被拒絕。"
        )
    m = _COUNT.search(report.text())
    if not m:
        raise AiritiParseError(f"無法從「{_text(report)}」解析總筆數。")
    total = int(m.group(1).replace(",", ""))

    anchors = [a for a in tree.css("h3.ustyle_heading_H3 a[onclick]") if "onclick" in a.attributes]
    items: list[SearchItem] = []
    for a in anchors:
        oc = a.attributes.get("onclick") or ""
        dm = _ONCLICK_DOCID.search(oc)
        if not dm:
            raise AiritiParseError(f"篇名連結的 onclick 抓不到 DocID：{oc[:80]!r}")
        onclick_id = dm.group(1)

        # The item container is the nearest ancestor that holds this item's own checkbox
        # (works for 1 result as well as 10; never climbs up to the whole page).
        container = next((n for n in _ancestors(a) if n.css_first("input.Result_選擇") is not None), None)
        if container is None or len(container.css("h3.ustyle_heading_H3 a[onclick]")) != 1:
            raise AiritiParseError(f"找不到結果項目容器（DocID {onclick_id}）。結果列表結構可能已改版。")

        # The checkbox key holds the raw DocID; the onclick copy is percent-encoded by the site
        # (e.g. "+" -> "%2b"), so prefer the checkbox and only fall back to decoding the onclick.
        box = container.css_first("input.Result_選擇")
        doc_id = (box.attributes.get("key") if box else None) or unquote(onclick_id)

        authors = [_text(x) for x in container.css("span.author a")]
        src = container.css_first("span.source")
        parts = [
            _text(src.css_first(sel)) if src else ""
            for sel in ("span.sourceTitleName", "span.sourcePub", "span.sourcedate", "span.sourcePageRange")
        ]
        source = " ".join(p for p in parts if p) or _text(src)
        tags = [_text(x) for x in container.css("li.preTag")]
        kind = next((t for t in tags if t and t != "OpenAccess"), "")
        items.append(
            SearchItem(
                doc_id=doc_id,
                title=_text(a),
                authors=authors,
                source=source,
                kind=kind,
                url=detail_url(doc_id),
                open_access="OpenAccess" in tags,
            )
        )

    if total > 0 and not items:
        raise AiritiParseError(f"頁面宣稱共 {total} 筆，但一筆結果都解析不出來（結果列表結構可能已改版）。")
    return SearchPage(total=total, items=items)


# ---------------------------------------------------------------- detail ----
@dataclass
class Reference:
    seq: str
    text: str
    doc_id: str  # Airiti DocID of the cited work, "" if it is not in Airiti
    doi: str


@dataclass
class Detail:
    doc_id: str
    url: str
    title: str
    authors: list[str]
    journal: str
    volume: str
    issue: str
    first_page: str
    last_page: str
    date: str
    doi: str
    issn: str
    publisher: str
    keywords_zh: list[str]
    keywords_en: list[str]
    abstract: str
    abstract_parallel: str
    abstract_is_truncated: bool  # True if only the (truncated) meta abstract was available
    notes: list[str] = field(default_factory=list)  # things that were missing, stated explicitly
    references: list[Reference] | None = None  # None = not requested / not parsed
    cited_by: list[Reference] | None = None  # works in Airiti that cite this one; None = not requested
    title_parallel: str = ""  # 次要篇名: English title for a Chinese article, Chinese title for an English one
    language: str = ""
    open_access: bool = False
    fulltext_marker: bool = False  # page says 可全文下載 (a label only; access may still need login/IP)


THESIS_JOURNAL_SUFFIXES = ("學位論文", "碩士論文", "博士論文", "碩士班論文", "博士班論文")


def is_thesis_detail(d: Detail) -> bool:
    """A thesis has a DocID starting with U..., and its "journal" field is a department name ending in ...論文."""
    return bool(re.match(r"U\d", d.doc_id)) or d.journal.endswith(THESIS_JOURNAL_SUFFIXES)


def _meta_all(tree: LexborHTMLParser, name: str) -> list[str]:
    out = []
    for n in tree.css("meta"):
        a = n.attributes
        if (a.get("name") == name or a.get("property") == name) and a.get("content"):
            out.append(a["content"].strip())
    return out


def _meta(tree: LexborHTMLParser, name: str) -> str:
    v = _meta_all(tree, name)
    return v[0] if v else ""


_KW_SPLIT = re.compile(r"[;；]")
_CJK = re.compile(r"[㐀-鿿]")
_ABSTRACT_TITLES = ("摘要", "Abstracts", "Abstract")
_PARALLEL_TITLES = ("並列摘要", "Parallel abstracts", "Parallel abstract")


def _block_by_heading(tree: LexborHTMLParser, titles: tuple[str, ...]) -> LexborNode | None:
    """Fallback: find an h3 whose own text equals one of `titles`, return its container."""
    for h in tree.css("h3"):
        if _text(h) in titles:
            return h.parent
    return None


def _abstract_block(tree: LexborHTMLParser, css: str, titles: tuple[str, ...]) -> tuple[str, list[str]]:
    node = tree.css_first(css) or _block_by_heading(tree, titles)
    if node is None:
        return "", []
    paras = [_text(p) for p in node.css("div.academicAbstractGroup p")] or [_text(p) for p in node.css("p")]
    kws = [_text(a) for a in node.css("div.academicKeywordGroup a")]
    return "\n\n".join(p for p in paras if p), [k for k in kws if k]


def parse_detail(html: str, doc_id: str, with_references: bool = False, with_cited_by: bool = False) -> Detail:
    tree = LexborHTMLParser(html)
    title = _meta(tree, "citation_title")
    if not title:
        raise AiritiShellPageError(
            f"DocID「{doc_id}」回傳的不是文獻詳目頁（沒有 citation_title；華藝對不存在的 DocID 會回傳首頁殼，偶爾對有效的 DocID 也會）。"
            "請確認 DocID 是否完整、是否由 airiti_search 取得；若確定存在，則可能是華藝改版。"
        )

    notes: list[str] = []
    abstract, kw_zh_body = _abstract_block(tree, "div.academicAbstractSet", _ABSTRACT_TITLES)
    parallel, kw_en_body = _abstract_block(tree, "div.academicAlignAbstractSet", _PARALLEL_TITLES)

    truncated = False
    if not abstract:
        meta_abs = _meta(tree, "citation_abstract")
        if meta_abs:
            abstract, truncated = meta_abs, True
            notes.append("頁面內文找不到完整摘要區塊，以下摘要來自 meta citation_abstract，可能被截斷。")
        else:
            notes.append("此文獻頁面沒有摘要（內文與 meta 皆無）。")
    if not parallel:
        notes.append("此文獻沒有並列（英文）摘要。")

    # keywords: meta is "中文;中文｜English;English"; body links are the fallback
    kw_zh: list[str] = []
    kw_en: list[str] = []
    meta_kw = _meta(tree, "citation_keywords")
    if meta_kw:
        if "｜" not in meta_kw and not _CJK.search(meta_kw):
            zh_part, en_part = "", meta_kw  # English-only record: no 中英 separator
        else:
            zh_part, _, en_part = meta_kw.partition("｜")
        kw_zh = [k.strip() for k in _KW_SPLIT.split(zh_part) if k.strip()]
        kw_en = [k.strip() for k in _KW_SPLIT.split(en_part) if k.strip()]
    if not kw_zh:
        kw_zh = kw_zh_body
    if not kw_en:
        kw_en = kw_en_body
    if not kw_zh and not kw_en:
        notes.append("此文獻沒有關鍵字。")

    references = parse_references(tree, html, notes) if with_references else None
    cited_by = parse_cited_by(html, notes) if with_cited_by else None
    em = _TITLE_EN_VAR.search(html)
    title_parallel = unquote(em.group(1)).strip() if em else ""
    tags = [_text(x) for x in tree.css("li.preTag")]

    return Detail(
        doc_id=doc_id,
        url=detail_url(doc_id),
        title=title,
        authors=_meta_all(tree, "citation_author"),
        journal=_meta(tree, "citation_journal_title"),
        volume=_meta(tree, "citation_volume"),
        issue=_meta(tree, "citation_issue"),
        first_page=_meta(tree, "citation_firstpage"),
        last_page=_meta(tree, "citation_lastpage"),
        date=_meta(tree, "citation_date"),
        doi=_meta(tree, "citation_doi"),
        issn=_meta(tree, "citation_issn"),
        publisher=_meta(tree, "citation_publisher"),
        keywords_zh=kw_zh,
        keywords_en=kw_en,
        abstract=abstract,
        abstract_parallel=parallel,
        abstract_is_truncated=truncated,
        notes=notes,
        references=references,
        cited_by=cited_by,
        title_parallel=title_parallel,
        language=_meta(tree, "citation_language"),
        open_access="OpenAccess" in tags,
        fulltext_marker=tree.css_first(".downloadPoint.可全文下載") is not None,
    )


# ------------------------------------------------------------ references ----
_REF_VAR = re.compile(r"var\s+全域_參考文獻\s*=\s*'([^']*)'")
_REF_COUNT = re.compile(r"顯示所有\s*(\d+)\s*篇參考文獻")  # 顯示所有 N 篇參考文獻


_TITLE_EN_VAR = re.compile(r"var\s+全域_編碼次要篇名\s*=\s*'([^']*)'")
_CITED_VAR = re.compile(r"var\s+全域_被引用記錄\s*=\s*'([^']*)'")
_TAG = re.compile(r"<[^>]+>")


def _read_ref_list(html: str, var: "re.Pattern[str]", what: str, notes: list[str]) -> list[Reference]:
    """Read a URL-encoded JSON list from a page JS variable. Missing variable -> explicit note;
    present-but-unparseable -> AiritiParseError (never a guess)."""
    m = var.search(html)
    if not m:
        notes.append(f"頁面沒有{what}資料（找不到對應的頁面變數）：可能此文獻沒有，或華藝改版。")
        return []
    raw = unquote(m.group(1)).strip()
    if not raw:  # empty variable = nothing to list
        return []
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("not a list")
    except ValueError as e:
        raise AiritiParseError(f"{what}資料存在但無法解析（{e}）。可能是華藝改版。") from e
    refs = [
        Reference(
            seq=str(r.get("Seq") or ""),
            text=re.sub(r"\s+", " ", unescape(_TAG.sub("", str(r.get("Reference") or "")))).strip(),
            doc_id=str(r.get("RDocID") or ""),
            doi=str(r.get("RDOI") or ""),
        )
        for r in rows
        if isinstance(r, dict)
    ]
    return [r for r in refs if r.text]


def parse_cited_by(html: str, notes: list[str]) -> list[Reference]:
    refs = _read_ref_list(html, _CITED_VAR, "被引用記錄", notes)
    if not refs and _CITED_VAR.search(html):
        notes.append("華藝沒有收錄引用此文獻的記錄（被引用 0 筆）。")
    return refs


def parse_references(tree: LexborHTMLParser, html: str, notes: list[str]) -> list[Reference]:
    """References come from the JS variable `全域_參考文獻`; the page also prints
    "顯示所有 N 篇參考文獻", used to cross-check completeness."""
    refs = _read_ref_list(html, _REF_VAR, "參考文獻", notes)
    if not refs:
        if _REF_VAR.search(html):
            notes.append("此文獻沒有參考文獻。")
        return []
    block = tree.css_first("#Detail_參考文獻_錨點")
    cm = _REF_COUNT.search(block.text()) if block else None
    if cm and int(cm.group(1)) != len(refs):
        notes.append(f"參考文獻筆數不一致：頁面標示 {cm.group(1)} 篇，實際解析到 {len(refs)} 篇，清單可能不完整。")
    elif not cm:
        notes.append("頁面沒有標示參考文獻總數，無法核對清單是否完整。")
    return refs
