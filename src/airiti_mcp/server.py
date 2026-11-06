"""Tool definitions. Transport-agnostic: import `mcp` from here and run it with any
transport (see stdio.py; a future http.py only needs `mcp.run("streamable-http")`)."""
from __future__ import annotations

import functools
import logging
import re
from datetime import datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import cite_export, client, hints, parsers, search_export
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

    html = await client.fetch_search_html(query, page, max_results)
    result = parsers.parse_search(html)

    if result.total == 0 and page > 1:
        # The site answers an out-of-range page with the same "0 筆" block as a truly empty
        # query, so on page > 1 we cannot tell the two apart without another request.
        return (
            f"查詢「{query}」第 {page} 頁（每頁 {max_results} 筆）：華藝回報 0 筆。"
            "這通常是頁碼超出總頁數，**不代表整個查詢沒有結果**；"
            "請改查 page=1 取得總筆數與總頁數。"
        )
    if result.total == 0:
        return (
            f"查詢「{query}」：華藝回報 0 筆結果（連線與解析皆正常，確實沒有符合的文獻）。\n"
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


MAX_EXPORT = 20  # tested live with 13 at once; kept modest so one call stays fast


async def _export_ids(ids: list[str], site_type: str, attempts: int = 2) -> tuple[str | None, str]:
    """One export call, retried (the site was seen failing once with an empty result, then succeeding)."""
    site_msg = ""
    for _ in range(attempts):
        body = await client.fetch_cite_export(ids, site_type)
        content = cite_export.parse_cite_export(body)
        if content:
            return content, ""
        site_msg = cite_export.cite_export_error(body)
    return None, site_msg


def _is_thesis(it: parsers.SearchItem) -> bool:
    return "學位論文" in (it.kind or "") or bool(re.match(r"U\d", it.doc_id))


@_tool
async def airiti_export_citation(doc_ids: list[str] | str, format: str = "bibtex") -> str:
    """取得華藝官方匯出的書目檔內容（一次可多篇，合成一個檔），可存檔匯入 Zotero、Mendeley、EndNote 等。

    Args:
        doc_ids: 華藝 DocID 清單，最多 20 個（由 airiti_search 取得）；只匯出一篇時也可直接傳一個字串。
        format: bibtex（預設）、ris、endnote 其中之一。
    內容原樣來自華藝（含中英文關鍵字、摘要、DOI），本工具不修改；請把「```」之間的文字存成對應副檔名。
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

    content, site_msg = await _export_ids(ids, site_type)
    if not content:
        hint = "請確認每個 DocID 都正確；U 開頭多半是學位論文，華藝已分到 airitietds.com。批次失敗時可改單篇匯出找出無效的 DocID。"
        raise AiritiError(f"華藝沒有回傳 {desc} 內容（重試一次仍失敗）。站方訊息：{site_msg or '（無）'}。{hint}")
    name = ids[0] if len(ids) == 1 else f"airiti_export_{len(ids)}篇"
    return f"{desc}，共 {len(ids)} 篇，建議存成 {name}.{ext}：\n```\n{content}\n```"


MAX_TOTAL_DEFAULT = 1000
MAX_TOTAL_LIMIT = 2000


@_tool
async def airiti_search_all(query: str, max_total: int = MAX_TOTAL_DEFAULT, export_format: str = "") -> str:
    """抓完一個查詢的所有結果頁，存成 CSV 清單與檢索紀錄檔（適合系統性文獻回顧）。

    Args:
        query: 搜尋關鍵字，空白分隔（每個詞都要符合），例如「鷹架 數學解題」。
        max_total: 最多取得幾筆，1–2000，預設 1000；總筆數超過時結果會被截斷，紀錄檔會註明。
        export_format: 選填，bibtex／ris／endnote 其中之一；填了會把可匯出的文獻（學位論文除外）每 20 篇一批
            向華藝匯出，合併成同一資料夾裡的一個檔（export.bib／export.ris／export.enw）。預設空白＝不匯出。
            每 20 篇約多花 3 秒；若某批失敗會逐篇重試以找出無效的 DocID，並記在檢索紀錄。
    檔案寫到固定資料夾（預設為使用者家目錄下的 airiti_exports，可用環境變數 AIRITI_EXPORT_DIR 更改），
    每次檢索一個新的子資料夾。只回傳摘要與檔案路徑，不把全部結果放進對話；
    只想匯出特定幾篇時，可改用 airiti_export_citation。
    """
    query = (query or "").strip()
    if not query:
        raise AiritiError("query 不能是空的。")
    if not 1 <= max_total <= MAX_TOTAL_LIMIT:
        raise AiritiError(f"max_total 必須介於 1 到 {MAX_TOTAL_LIMIT}。")
    export_key = (export_format or "").strip().lower()
    if export_key and export_key not in cite_export.FORMATS:
        raise AiritiError(f"不支援的 export_format「{export_format}」。可用：{'、'.join(cite_export.FORMATS)}，或留空不匯出。")

    when = datetime.now().astimezone()
    items: list[parsers.SearchItem] = []
    fetched = pages_fetched = 0
    error = ""
    first = parsers.parse_search(await client.fetch_search_html(query, 1, MAX_PAGE_SIZE))
    total = first.total
    if total == 0:
        return (
            f"查詢「{query}」：華藝回報 0 筆結果，沒有產生檔案。\n" + hints.few_results_hint(query, 0)
        )
    pages_fetched, fetched, items = 1, len(first.items), list(first.items)
    pages_needed = -(-min(total, max_total) // MAX_PAGE_SIZE)
    for page in range(2, pages_needed + 1):
        try:
            res = parsers.parse_search(await client.fetch_search_html(query, page, MAX_PAGE_SIZE))
        except AiritiError as e:  # keep what we have; the log says the run is incomplete
            error = f"第 {page} 頁：{type(e).__name__}: {e}"
            break
        if not res.items:
            error = f"第 {page} 頁沒有項目（華藝回報 {res.total} 筆），提前停止。"
            break
        pages_fetched += 1
        fetched += len(res.items)
        items += res.items
    seen: set[str] = set()
    deduped = [it for it in items if not (it.doc_id in seen or seen.add(it.doc_id))]
    unique_items = deduped[:max_total]

    folder = search_export.export_root() / search_export.run_folder_name(query, when)
    folder.mkdir(parents=True, exist_ok=True)
    csv_path, log_path = folder / "results.csv", folder / "search_log.txt"

    export_lines: list[str] = []
    export_path = None
    export_problem = ""
    if export_key:
        site_type, ext, desc = cite_export.FORMATS[export_key]
        ids = [it.doc_id for it in unique_items if not _is_thesis(it)]
        skipped = len(unique_items) - len(ids)
        parts: list[str] = []
        done: set[str] = set()  # DocIDs that made it into the file
        failed: list[str] = []
        unattempted: list[str] = []  # never requested because the run stopped early
        for k in range(0, len(ids), MAX_EXPORT):
            batch = ids[k : k + MAX_EXPORT]
            try:
                content, _ = await _export_ids(batch, site_type)
                if content:
                    parts.append(content)
                    done.update(batch)
                    continue
                if len(batch) == 1:  # already tried twice; nothing left to isolate
                    failed.append(batch[0])
                    continue
                for one in batch:  # one invalid DocID fails the whole batch: retry singly (once) to isolate it
                    c, _ = await _export_ids([one], site_type, attempts=1)
                    if c:
                        parts.append(c)
                        done.add(one)
                    else:
                        failed.append(one)
            except AiritiError as e:  # network/parse trouble: stop, keep what was exported
                unattempted = [i for i in ids[k:] if i not in done and i not in failed]
                export_problem = f"{type(e).__name__}: {e}"
                break
        exported = len(done)
        if parts:
            export_path = folder / f"export.{ext}"
            with export_path.open("w", encoding="utf-8", newline="") as f:
                f.write("\r\n\r\n".join(parts) + "\r\n")
        export_lines = [
            f"書目匯出：{desc}，成功 {exported} 篇 → {export_path.name if export_path else '（未產生檔案）'}",
            f"  略過學位論文 {skipped} 篇（無法匯出）",
        ]
        if failed:
            shown = '、'.join(failed[:20]) + (f"…（另 {len(failed) - 20} 篇見匯出前的清單）" if len(failed) > 20 else "")
            export_lines.append(f"  【匯出失敗】{len(failed)} 篇（華藝拒絕這些 DocID）：{shown}")
        if unattempted:
            export_lines.append(f"  【因中斷未處理】{len(unattempted)} 篇（沒有發出匯出請求，不代表 DocID 無效）")
        if export_problem:
            export_lines.append(f"  【匯出中斷】{export_problem}")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: BOM so Excel reads UTF-8
        f.write(search_export.build_csv(unique_items))
    log_path.write_text(
        search_export.build_log(
            query=query, when=when, reported_total=total, fetched=fetched, deduped=len(deduped), unique=len(unique_items),
            pages_fetched=pages_fetched, page_size=MAX_PAGE_SIZE, cap=max_total, items=unique_items, error=error,
            export_lines=export_lines,
        ),
        encoding="utf-8",
    )
    out = [
        f"查詢「{query}」：華藝回報共 {total} 筆；取得 {len(unique_items)} 筆（{pages_fetched} 頁）。",
        f"清單：{csv_path}",
        f"檢索紀錄：{log_path}",
    ]
    if export_key:
        out += [f"書目檔：{export_path}" if export_path else "書目檔：（未產生）"] + [l.strip() for l in export_lines]
    if error:
        out.append(f"【不完整】抓取中途失敗，已存下已取得的部分。{error}")
    if total > max_total:
        out.append(f"【截斷】總筆數 {total} 超過 max_total={max_total}；建議加關鍵字縮小範圍，或調高 max_total（上限 {MAX_TOTAL_LIMIT}）。")
    out += ["", "前 5 筆："] + [f"{i}. {it.title}（DocID：{it.doc_id}）" for i, it in enumerate(unique_items[:5], 1)]
    return "\n".join(out)
