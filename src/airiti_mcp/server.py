"""Tool definitions. Transport-agnostic: import `mcp` from here and run it with any
transport (see stdio.py; a future http.py only needs `mcp.run("streamable-http")`)."""
from __future__ import annotations

import functools
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import client, hints, parsers
from .cite import apa7
from .errors import AiritiError

mcp = MCPServer(
    "airiti-library",
    instructions=(
        "查詢華藝線上圖書館（Airiti Library）的文獻書目與摘要。只提供檢索與摘要，不提供全文。"
        "流程：先用 airiti_search 找文獻拿到 DocID，再用 airiti_abstract 讀摘要。"
    ),
)

# httpx logs full request URLs (incl. the whole query JSON) at INFO; keep stderr quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)

MAX_PAGE_SIZE = 20


def _tool(fn):
    """Register as an MCP tool, surfacing AiritiError text to the model.

    MCP SDK v2 hides the message of every exception except ToolError ("Error executing tool X"),
    which would defeat our network / parse / bad-input distinctions."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except AiritiError as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e

    return mcp.tool()(wrapper)


def _check_doc_id(doc_id: str) -> str:
    doc_id = (doc_id or "").strip()
    if not doc_id:
        raise AiritiError("doc_id 不能是空的。請先用 airiti_search 取得 DocID。")
    return doc_id


@_tool
async def airiti_search(query: str, page: int = 1, max_results: int = 10) -> str:
    """搜尋華藝線上圖書館的文獻（所有欄位）。多個關鍵字以空白分隔。

    Args:
        query: 搜尋關鍵字，例如「鷹架 數學解題」。
        page: 頁碼，從 1 開始（每次呼叫只抓一頁）。
        max_results: 每頁筆數，1–20，預設 10。
    回傳總筆數，以及本頁每筆的篇名、作者、出處、DocID、詳目頁網址。
    """
    query = (query or "").strip()
    if not query:
        raise AiritiError("query 不能是空的。")
    if page < 1:
        raise AiritiError("page 必須 >= 1。")
    if not 1 <= max_results <= MAX_PAGE_SIZE:
        raise AiritiError(f"max_results 必須介於 1 到 {MAX_PAGE_SIZE}。")

    html = await client.fetch_search_html(query, page, max_results)
    result = parsers.parse_search(html)

    if result.total == 0:
        return (
            f"查詢「{query}」：華藝回報 0 筆結果（連線與解析皆正常，確實沒有符合的文獻）。\n"
            + hints.few_results_hint(query, 0)
        )
    if not result.items:  # unreachable (parser raises), kept as a guard
        raise AiritiError("解析結果異常：有總筆數但沒有項目。")

    pages = -(-result.total // max_results)
    lines = [f"查詢「{query}」：共 {result.total} 筆；第 {page}/{pages} 頁（每頁 {max_results} 筆），本頁 {len(result.items)} 筆。"]
    if page > pages:
        lines.append("（頁碼超出範圍，本頁沒有資料。）")
    for i, it in enumerate(result.items, start=(page - 1) * max_results + 1):
        lines += [
            "",
            f"{i}. {it.title}" + (f"  [{it.kind}]" if it.kind else ""),
            f"   作者：{'、'.join(it.authors) if it.authors else '（頁面未列出）'}",
            f"   出處：{it.source or '（頁面未列出）'}",
            f"   DocID：{it.doc_id}",
            f"   網址：{it.url}",
        ]
    if result.total < hints.FEW_RESULTS:
        lines += ["", hints.few_results_hint(query, result.total)]
    return "\n".join(lines)


async def _detail(doc_id: str, with_references: bool = False) -> parsers.Detail:
    doc_id = _check_doc_id(doc_id)
    return parsers.parse_detail(await client.fetch_detail_html(doc_id), doc_id, with_references)


@_tool
async def airiti_abstract(doc_id: str, include_references: bool = False) -> str:
    """讀取一篇華藝文獻的完整書目、中文摘要、英文並列摘要與中英關鍵字。

    Args:
        doc_id: 華藝 DocID（由 airiti_search 取得，格式不固定，請原樣傳入）。
        include_references: 是否附上參考文獻清單，預設 False（清單可能很長）。
            清單中若被引用文獻也收錄於華藝，會標出其 DocID，可再丟給本工具。
    """
    d = await _detail(doc_id, include_references)
    vol_issue = "".join([f"第{d.volume}卷" if d.volume else "", f"第{d.issue}期" if d.issue else ""])
    pages = f"{d.first_page}–{d.last_page}" if d.first_page and d.last_page else d.first_page
    rows = [
        ("篇名", d.title),
        ("作者", "、".join(d.authors)),
        ("期刊", d.journal),
        ("卷期", vol_issue),
        ("頁碼", pages),
        ("日期", d.date),
        ("DOI", d.doi),
        ("ISSN", d.issn),
        ("出版者", d.publisher),
        ("DocID", d.doc_id),
        ("詳目頁", d.url),
    ]
    out = ["## 書目"] + [f"- {k}：{v}" for k, v in rows if v]
    out += ["", "## 摘要" + ("（注意：可能被截斷）" if d.abstract_is_truncated else ""), d.abstract or "（無）"]
    out += ["", "## 關鍵字", "、".join(d.keywords_zh) or "（無）"]
    out += ["", "## 並列摘要（英文）", d.abstract_parallel or "（無）"]
    out += ["", "## 並列關鍵字", "; ".join(d.keywords_en) or "（無）"]
    if d.references is not None:
        out += ["", f"## 參考文獻（{len(d.references)} 篇）"]
        for i, r in enumerate(d.references, 1):
            extra = [f"DocID：{r.doc_id}" if r.doc_id else "", f"DOI：{r.doi}" if r.doi else ""]
            extra = "；".join(e for e in extra if e)
            out.append(f"{i}. {r.text}" + (f"\n   （{extra}）" if extra else ""))
        if not d.references:
            out.append("（無）")
    if d.notes:
        out += ["", "## 備註"] + [f"- {n}" for n in d.notes]
    return "\n".join(out)


@_tool
async def airiti_cite_apa(doc_id: str) -> str:
    """用書目資料產生 APA 7 格式引用（目前只支援期刊論文）。

    Args:
        doc_id: 華藝 DocID。
    """
    return apa7(await _detail(doc_id))
