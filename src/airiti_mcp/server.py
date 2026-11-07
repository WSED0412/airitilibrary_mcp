"""Tool definitions. Transport-agnostic: import `mcp` from here and run it with any
transport (see stdio.py; a future http.py only needs `mcp.run("streamable-http")`)."""
from __future__ import annotations

import functools
import logging
import re
from datetime import datetime

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import cite_export, client, harvest, hints, parsers, search_export
from .cite import apa7
from .errors import AiritiError, AiritiParseError

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

    # The site sometimes answers a valid page with the same "0 筆" block a truly empty query gets, so an empty
    # answer is asked again before it is believed (page 1: twice; later pages may simply be out of range: once).
    waits = harvest.PAGE_RETRY_WAITS if page == 1 else harvest.PAGE_RETRY_WAITS[:1]
    result, err, retries = await harvest.fetch_search_page(query, page, max_results, waits=waits)
    if result is None:
        raise err  # type: ignore[misc]  # every try raised

    if result.total == 0 and page > 1:
        # The site answers an out-of-range page with the same "0 筆" block as a truly empty
        # query, so on page > 1 we cannot tell the two apart without another request.
        return (
            f"查詢「{query}」第 {page} 頁（每頁 {max_results} 筆）：華藝回報 0 筆（已重試 {retries} 次）。"
            "這通常是頁碼超出總頁數，**不代表整個查詢沒有結果**；"
            "請改查 page=1 取得總筆數與總頁數。華藝偶爾也會對有效頁碼暫時回 0 筆，確定頁碼在範圍內的話可稍後再試。"
        )
    if result.total == 0:
        return (
            f"查詢「{query}」：華藝回報 0 筆結果（已重試 {retries} 次仍是 0 筆，應該是真的沒有符合的文獻）。\n"
            + hints.few_results_hint(query, 0)
        )
    if not result.items:  # unreachable (parser raises), kept as a guard
        raise AiritiError("解析結果異常：有總筆數但沒有項目。")

    pages = -(-result.total // max_results)
    lines = [f"查詢「{query}」：共 {result.total} 筆；第 {page}/{pages} 頁（每頁 {max_results} 筆），本頁 {len(result.items)} 筆。"]
    for i, it in enumerate(result.items, start=(page - 1) * max_results + 1):
        lines += [
            "",
            f"{i}. {it.title}" + (f"  [{it.kind}]" if it.kind else "") + ("  [OpenAccess]" if it.open_access else ""),
            f"   作者：{'、'.join(it.authors) if it.authors else '（頁面未列出）'}",
            f"   出處：{it.source or '（頁面未列出）'}",
            f"   DocID：{it.doc_id}",
            f"   網址：{it.url}",
        ]
    if result.total < hints.FEW_RESULTS:
        lines += ["", hints.few_results_hint(query, result.total)]
    return "\n".join(lines)


async def _detail(doc_id: str, with_references: bool = False, with_cited_by: bool = False) -> parsers.Detail:
    doc_id = _check_doc_id(doc_id)
    html = await client.fetch_detail_html(doc_id)
    try:
        return parsers.parse_detail(html, doc_id, with_references, with_cited_by)
    except AiritiParseError as e:
        if re.match(r"U\d", doc_id):
            raise AiritiParseError(
                f"{e} 另外，U 開頭的 DocID 多半是學位論文；華藝已把學位論文分到 airitietds.com，"
                "airitilibrary.com 目前讀不到這類詳目頁。"
            ) from e
        raise


def _render_refs(title: str, refs: list[parsers.Reference]) -> list[str]:
    out = ["", f"## {title}（{len(refs)} 篇）"]
    for i, r in enumerate(refs, 1):
        extra = [f"DocID：{r.doc_id}" if r.doc_id else "", f"DOI：{r.doi}" if r.doi else ""]
        extra = "；".join(e for e in extra if e)
        out.append(f"{i}. {r.text}" + (f"{chr(10)}   （{extra}）" if extra else ""))
    if not refs:
        out.append("（無）")
    return out


@_tool
async def airiti_abstract(doc_id: str, include_references: bool = False, include_cited_by: bool = False) -> str:
    """讀取一篇華藝文獻的完整書目、中文摘要、英文並列摘要與中英關鍵字。

    Args:
        doc_id: 華藝 DocID（由 airiti_search 取得，格式不固定，請原樣傳入）。
        include_references: 是否附上參考文獻清單，預設 False（清單可能很長）。
            清單中若被引用文獻也收錄於華藝，會標出其 DocID，可再丟給本工具。
        include_cited_by: 是否附上「被引用記錄」（華藝收錄的、引用了這篇的文獻），預設 False。
    """
    d = await _detail(doc_id, include_references, include_cited_by)
    vol_issue = "".join([f"第{d.volume}卷" if d.volume else "", f"第{d.issue}期" if d.issue else ""])
    pages = f"{d.first_page}–{d.last_page}" if d.first_page and d.last_page else d.first_page
    rows = [
        ("篇名", d.title),
        ("並列篇名", d.title_parallel),
        ("作者", "、".join(d.authors)),
        ("期刊", d.journal),
        ("卷期", vol_issue),
        ("頁碼", pages),
        ("日期", d.date),
        ("DOI", d.doi),
        ("ISSN", d.issn),
        ("出版者", d.publisher),
        ("語言", d.language),
        ("開放取用", "是（OpenAccess）" if d.open_access else ""),
        ("全文", "頁面標示可全文下載（僅為標記，是否能取得視華藝權限而定）" if d.fulltext_marker else ""),
        ("DocID", d.doc_id),
        ("詳目頁", d.url),
    ]
    out = ["## 書目"] + [f"- {k}：{v}" for k, v in rows if v]
    out += ["", "## 摘要" + ("（注意：可能被截斷）" if d.abstract_is_truncated else ""), d.abstract or "（無）"]
    out += ["", "## 關鍵字", "、".join(d.keywords_zh) or "（無）"]
    out += ["", "## 並列摘要（英文）", d.abstract_parallel or "（無）"]
    out += ["", "## 並列關鍵字", "; ".join(d.keywords_en) or "（無）"]
    if d.references is not None:
        out += _render_refs("參考文獻", d.references)
    if d.cited_by is not None:
        out += _render_refs("被引用記錄（華藝收錄的引用文獻）", d.cited_by)
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


MAX_EXPORT = harvest.MAX_EXPORT


@_tool
async def airiti_export_citation(doc_ids: list[str] | str, format: str = "bibtex", save: bool = False) -> str:
    """取得華藝官方匯出的書目檔內容（一次可多篇，合成一個檔），可存檔匯入 Zotero、Mendeley、EndNote 等。

    Args:
        doc_ids: 華藝 DocID 清單，最多 20 個（由 airiti_search 取得）；只匯出一篇時也可直接傳一個字串。
        format: bibtex（預設）、ris、endnote 其中之一。
        save: 預設 False＝把全文放進回傳內容（20 篇約 3–4 萬字元，會佔用大量對話上下文）。
            True＝改成直接存成檔案（存到匯出資料夾下新的子資料夾），只回傳檔案路徑與篇數，不把內容放進對話。
    內容來自華藝（含中英文關鍵字、摘要、DOI），本工具只做一項修改：RIS 裡沒有 DOI 的記錄，華藝給的 UR 是相對路徑
    （Article/Detail?docID=…，匯入後連結會壞），這裡補成完整網址；其餘原樣。請把「```」之間的文字存成對應副檔名。
    注意：只要清單裡有一個 DocID 無效，華藝會整批失敗且不指出是哪一個。
    """
    key = (format or "").strip().lower()
    if key not in cite_export.FORMATS:
        raise AiritiError(f"不支援的 format「{format}」。可用：{'、'.join(cite_export.FORMATS)}。")
    if isinstance(doc_ids, str):
        doc_ids = [doc_ids]
    ids = list(dict.fromkeys(_check_doc_id(d) for d in (doc_ids or [])))  # de-duplicated, order kept
    if not ids:
        raise AiritiError("doc_ids 不能是空的。請先用 airiti_search 取得 DocID。")
    if len(ids) > MAX_EXPORT:
        raise AiritiError(f"一次最多 {MAX_EXPORT} 篇（收到 {len(ids)} 篇），請分批呼叫。")
    site_type, ext, desc = cite_export.FORMATS[key]

    content, site_msg = await harvest.export_ids(ids, site_type)
    if not content:
        hint = "請確認每個 DocID 都正確；U 開頭多半是學位論文，華藝已分到 airitietds.com。批次失敗時可改單篇匯出找出無效的 DocID。"
        raise AiritiError(f"華藝沒有回傳 {desc} 內容（重試一次仍失敗）。站方訊息：{site_msg or '（無）'}。{hint}")
    if save:
        folder = search_export.make_export_folder(f"export_{len(ids)}", datetime.now().astimezone())
        path = folder / f"export.{ext}"
        try:
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write(content + "\r\n")
        except BaseException:  # disk full, permission, ... : leave nothing behind, then let the error through
            path.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError:
                pass
            raise
        return f"{desc}，共 {len(ids)} 篇，已存檔（內容沒有放進對話）：{path}"
    name = ids[0] if len(ids) == 1 else f"airiti_export_{len(ids)}篇"
    return f"{desc}，共 {len(ids)} 篇，建議存成 {name}.{ext}：\n```\n{content}\n```"


MAX_TOTAL_DEFAULT = 1000
MAX_TOTAL_LIMIT = 2000
DEFAULT_BUDGET_S = 90  # stay under the ~2 min mark where clients start backgrounding or timing out tool calls
MIN_BUDGET_S, MAX_BUDGET_S = 10, 900


@_tool
async def airiti_search_all(
    query: str = "",
    max_total: int = MAX_TOTAL_DEFAULT,
    export_format: str = "",
    resume: str = "",
    time_budget_s: int = DEFAULT_BUDGET_S,
    ctx: Context | None = None,
) -> str:
    """抓完一個查詢的所有結果頁，存成 CSV 清單與檢索紀錄檔（適合系統性文獻回顧）。

    Args:
        query: 搜尋關鍵字，空白分隔（每個詞都要符合），例如「鷹架 數學解題」。續跑時不用填。
        max_total: 最多取得幾筆，1–2000，預設 1000；總筆數超過時結果會被截斷，紀錄檔會註明。
        export_format: 選填，bibtex／ris／endnote 其中之一；填了會把可匯出的文獻（學位論文除外）每 20 篇一批
            向華藝匯出，合併成同資料夾裡的一個檔（export.bib／export.ris／export.enw）。預設空白＝不匯出。
        resume: 續跑用。若上次回傳【已暫停】，把它給的資料夾名稱填在這裡再呼叫一次，其他參數不用重填。
        time_budget_s: 單次呼叫最多工作幾秒（10–900，預設 90）。時間到會乾淨暫停並存下進度，避免被客戶端逾時中斷；
            量大時會分成多次呼叫，請重複呼叫直到不再出現【已暫停】。
    檔案寫到固定資料夾（預設為使用者家目錄下的 airiti_exports，可用環境變數 AIRITI_EXPORT_DIR 更改），
    每次檢索一個新的子資料夾（含 state.json 進度檔）。只回傳摘要與檔案路徑，不把全部結果放進對話；
    只想匯出特定幾篇時，可改用 airiti_export_citation。
    """
    if not MIN_BUDGET_S <= time_budget_s <= MAX_BUDGET_S:
        raise AiritiError(f"time_budget_s 必須介於 {MIN_BUDGET_S} 到 {MAX_BUDGET_S}。")

    async def progress(done: float, total: float | None, message: str) -> None:
        if ctx is not None:  # a no-op on the wire unless the client asked for progress
            try:
                await ctx.report_progress(done, total, message)
            except Exception:  # progress is a courtesy; never let it kill a long run
                logging.getLogger(__name__).debug("progress report failed", exc_info=True)

    st = None
    spent = 0.0
    if resume.strip():
        folder = harvest.resolve_run_folder(resume)
    else:
        query = (query or "").strip()
        if not query:
            raise AiritiError("query 不能是空的（若要續跑，請填 resume）。")
        if not 1 <= max_total <= MAX_TOTAL_LIMIT:
            raise AiritiError(f"max_total 必須介於 1 到 {MAX_TOTAL_LIMIT}。")
        export_key = (export_format or "").strip().lower()
        if export_key and export_key not in cite_export.FORMATS:
            raise AiritiError(f"不支援的 export_format「{export_format}」。可用：{'、'.join(cite_export.FORMATS)}，或留空不匯出。")
        t_call = harvest.now()
        st, hint = await harvest.start_run(query, max_total, export_key)
        spent = harvest.now() - t_call  # fetching page 1 counts against this call's budget
        if st is None:
            return f"查詢「{query}」：華藝回報 0 筆結果，沒有產生檔案。\n" + hint
        folder = search_export.export_root() / search_export.run_folder_name(query, datetime.fromisoformat(st.started))
        folder.mkdir(parents=True, exist_ok=True)
        harvest.save_state(st, folder)

    lock = harvest.run_lock(folder.name)
    if lock.locked():  # never advance one run from two calls at once (they would repeat each other's work)
        return f"檢索「{folder.name}」正在另一個呼叫裡執行中，請等它回傳後再用 resume 呼叫，不要同時跑兩個。"
    async with lock:
        if st is None:
            st = harvest.load_state(folder)  # read inside the lock: the previous call may have just finished
            if st.finished:  # nothing left to do: show the result again
                return harvest.summary(st, folder)
        finished = await harvest.advance(st, folder, time_budget_s, progress, spent_s=spent)
        return harvest.summary(st, folder, None if finished else time_budget_s)
