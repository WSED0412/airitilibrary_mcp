# airiti-mcp

**English** | [繁體中文](README.zh-TW.md)

A local MCP server (stdio) for the Airiti Library (華藝線上圖書館). It **only searches and reads abstracts**; it never downloads full text.

> ⚠️ **Read "Terms of use and use at your own risk" below before using this.** Airiti's *Copyright Notice and Website Terms of Use* restricts the use of "automated programs". The author cannot determine whether this project falls under that restriction. **You use it at your own risk.**

## Terms of use and use at your own risk (read first)

This project sends queries (search, abstract reading, citation export) to the Airiti Library on your behalf. Airiti's published
**[Copyright Notice and Website Terms of Use](https://www.airitilibrary.com/Home/Editor)** (in Chinese) restricts this kind of use. Please read the original text before using it.

**Article 4, verbatim** (read on 2026-11-07; the terms may change, so check Airiti's site for the current version). Translated from the original Chinese:

> The site also prohibits users, by means of automated programs executing queries, downloads, data scraping, content extraction, etc., from using the site or from loading the content included on the site into systems or databases developed by themselves or others; users also may not place data included on the site into their own or third parties' automated learning systems or data training platforms, or provide it to third parties for AI-related data processing and development.

Article 1 only permits "personal, non-commercial use", and only for searching, viewing, printing and downloading "through browsers, phones and other devices".

**The author's concerns (we are not sure, and draw no conclusion):**

- The author **cannot determine** whether this project counts as "running queries, downloads, data scraping or content extraction through automated programs". A single query initiated by a user leaves room for interpretation. Batch retrieval and batch export (`airiti_search_all`) are hard to describe as anything other than "data scraping".
- Whether handing query results (abstracts, bibliographic data) to an AI assistant counts as "providing it to third parties for AI-related data processing" is likewise unclear.
- The author is not a lawyer, and this is **not legal advice**. Whether this constitutes a violation is for Airiti (and the license agreement between your institution and Airiti) to determine.
- Airiti's [`robots.txt`](https://www.airitilibrary.com/robots.txt) also blocks a number of AI crawlers by name, and lists `Disallow: /Article/Query/` (the search function) for general crawlers.

**Use at your own risk:** You bear all risk of using this project (its code, documentation and any output). The author does not guarantee that it complies with Airiti's terms of use,
and **accepts no responsibility for any consequences**, including but not limited to restrictions on your account or IP, effects on your institution's license, and legal liability.

**Please observe these restrictions:**

1. **Do not use an automated program like this MCP server to query, download, scrape or extract content from the Airiti site, and above all do not load that content into systems or databases developed by yourself or others** (the former is precisely the point of uncertainty raised under "The author's concerns" above, and using the tool means accepting that risk yourself) (Airiti Article 4; this includes your own databases, knowledge bases and vector databases, and platforms developed by others).
2. **Do not use the extracted content to train or fine-tune models, place it in your own or third parties' automated learning systems or data training platforms, or provide it to third parties for AI-related data processing and development** (whether handing results to an AI assistant counts is subject to the same uncertainty noted above).
3. For **personal, non-commercial** research reference only. Do not reproduce, distribute or publicly transmit the content, or put it to any commercial use (Articles 1 and 2).
4. If in doubt, ask Airiti or your school's library first.

The project is designed with rate limiting (at least 1.5 seconds between any two requests), an honest User-Agent, no full-text access and no caching. These are **only measures to reduce the load on the site; they are not permission from Airiti**.

| Tool | Purpose |
|---|---|
| `airiti_search(query, page=1, max_results=10)` | Search (all fields). Returns the total count plus, for each result on the page, the title, authors, source, DocID and detail-page URL. `max_results` is 1–20; fetches one page per call. |
| `airiti_abstract(doc_id, include_references=False, include_cited_by=False)` | Full bibliographic record (including DOI, English title, language, OpenAccess and full-text-available flags), the Chinese abstract, the parallel English abstract, and Chinese and English keywords. Optional: `include_references` appends the reference list and `include_cited_by` appends the citing records Airiti has indexed (both off by default because the lists can be long; entries that have a DocID can be passed back to this tool). For journals without a volume number (Airiti often truncates the leading digits of the issue number shown on the page), it sends 2 extra requests and uses the issue number from the official export data instead. Theses and dissertations (學位論文) have no issue number and do not send the extra requests. |
| `airiti_cite_apa(doc_id)` | Generates an APA 7 citation in Chinese from the bibliographic data. **Chinese journal articles only** (the title and every author name must be Chinese). Theses and dissertations (學位論文) and English or other foreign-language articles are explicitly rejected, with a pointer to `airiti_export_citation` for exporting RIS / BibTeX into reference-manager software. For records without a volume number, Airiti often truncates the leading digits of the issue number on the page (all 9 articles tested were wrong), so the issue number from the official export data is used instead (2 extra requests; if the cross-check fails, the issue number is omitted). |
| `airiti_export_citation(doc_ids, format="bibtex", save=False)` | Returns the content of Airiti's official bibliographic export file. `doc_ids` is a list or a single string (up to 20 articles per call, merged into one file) and `format` is `bibtex`, `ris` or `endnote`; the file can be saved and imported into Zotero, Mendeley, EndNote and the like. The content comes from Airiti with **one modification only**: for RIS records without a DOI, the `UR` Airiti gives is a relative path (`Article/Detail?docID=…`, which breaks after import), so it is completed to a full URL here; everything else is untouched. It needs only 2 requests however many articles there are (up to 4 if the site occasionally returns empty and a retry is needed). Content over 30,000 characters (roughly 15 or more articles) is **automatically** saved as `export.<extension>` in a new subfolder of the export folder and only the path is returned, so it does not fill the conversation context; `save=True` always saves to a file. Note: a single invalid DocID in the list makes Airiti fail the whole batch. |
| `airiti_search_all(query, max_total=1000, export_format="", resume="", time_budget_s=90)` | Fetches every result page of one query (20 results per page, 1.5 seconds between pages) and writes `results.csv` (DocID, title [篇名], authors [作者], source [出處], type [類型], …, plus a "preprint" [預刊] column and a "suspected duplicate group" [疑似重複組] column: Airiti indexes the preprint and the formally published version of the same article as separate records, and records with the same title (full-width and half-width treated as equal) and authors but different DocIDs are tagged with the same group D1, D2, … (records without authors are not grouped). It **only tags and never removes**; which one to keep is your call) and `search_log.txt` (search date, query, total count, how many were removed by de-duplication and by truncation, number of suspected duplicate groups, and type distribution, which can go into a methods section). Files are saved to `~/airiti_exports/<time>_<keyword>/`; the location can be changed with the environment variable `AIRITI_EXPORT_DIR`, and the tool itself accepts no path. `max_total` is 1–2000. Optional `export_format` (`bibtex`, `ris` or `endnote`): exports the results (theses and dissertations [學位論文] included) from Airiti in batches of 20 and merges them into `export.bib` / `.ris` / `.enw` in the same folder; if a batch fails, it retries article by article to find the invalid DocIDs and records them in the search log. **Timeout protection**: a single call works for at most about `time_budget_s` seconds (10–900), then pauses cleanly and saves progress to `state.json`. It returns 【已暫停】 (paused) with the folder name; call again with `resume=<folder name>` to continue from the breakpoint (nothing is fetched or exported twice), and repeat until 【已暫停】 no longer appears. Transient errors (network etc.) do not end the search outright: it pauses with 【因錯誤暫停】 (paused on error) and `resume` retries the same step; only 3 consecutive failures make it give up and mark what it has as incomplete. The same search cannot be advanced by two calls at once (the second is rejected, to avoid duplicate exports), and `resume` accepts only a folder name inside the export folder (links pointing elsewhere are rejected). Progress is reported (MCP progress). **Pagination reliability** (Airiti's behavior as observed): a valid page number occasionally returns "0 results" (about 2%), and a page that comes back empty or fails is retried automatically after waiting 3 and 6 seconds. Records with equal rank are sorted randomly on every request, so across a page boundary "one record duplicated, one record missed" occurs; when adjacent pages are found to overlap, both pages are re-fetched to recover the missing record (up to 3 rounds per boundary, and recovered records are put back in their original rank). The log has an "integrity check" (expected count vs. actual distinct count, with the shortfall written out; it aborts and says so after 3 consecutive failed repair connections). A first page returning 0 results is also confirmed before saying "no results". Detail pages show the same kind of behavior (a valid DocID occasionally returns the home-page shell, about 3%), and `airiti_abstract` / `airiti_cite_apa` retry before saying "DocID does not exist". Returns only a summary and paths. |

## Behavior and limits

- It touches no full-text / PDF download endpoint and does **no caching** (pages are marked `noarchive`); every call queries live.
- At least 1.5 seconds between any two requests; 15-second timeout.
- The above are only designs to reduce the load on the site and **do not mean compliance with Airiti's terms of use**; see "Terms of use and use at your own risk" at the top.
- Errors are reported separately: `AiritiNetworkError` (cannot connect / timeout / non-200), `AiritiBlockedError` (HTTP 403 / 429, refused or rate-limited; **not retried automatically**, so wait a while and try again), `AiritiParseError` (the page structure does not match, e.g. after a redesign or when the DocID does not exist),
  and "0 results" (connection and parsing are fine and nothing matches). Fields that cannot be parsed are written as "（無）" ("none") with an explanatory note; nothing is guessed.
- The DocID format is not fixed (it may contain characters such as `+`). Always use it as-is, as obtained from the search results.

## Installation

Requires [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`).

```powershell
cd <this project folder>
uv sync
uv run pytest                      # offline tests (no network)
$env:AIRITI_LIVE=1; uv run pytest tests/test_live.py   # live tests (about 45 requests, a little over a minute; 1.5 seconds between requests)
```

## Register with Claude Code

```powershell
claude mcp add airiti -- uv --directory "<full path of the project folder>" run airiti-mcp
```

Add `--scope user` to use it in all projects. Check with `claude mcp list`.

## Register with Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config opens it directly) and add the following. A normal install keeps it in `%APPDATA%\Claude\`;
the Store (MSIX) version keeps it in `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`. Every `\` in a path inside the JSON must be written as `\\` (e.g. `C:\\Users\\you\\...`).

```json
{
  "mcpServers": {
    "airiti": {
      "command": "<full path of uv.exe>",
      "args": [
        "--directory",
        "<full path of the project folder>",
        "run",
        "airiti-mcp"
      ]
    }
  }
}
```

After saving, fully quit and reopen Claude Desktop. The desktop app does not necessarily inherit PATH, so use the absolute path of uv for `command` (`where uv` shows it).
If startup fails because the project path contains Chinese characters, move the project to an English-only path (such as `C:\mcp\airiti`) and update `--directory`.

## Testing with MCP Inspector

```powershell
# interactive UI
npx @modelcontextprotocol/inspector uv --directory "<project path>" run airiti-mcp
# command line
npx @modelcontextprotocol/inspector --cli uv --directory "<project path>" run airiti-mcp --method tools/list
npx @modelcontextprotocol/inspector --cli uv --directory "<project path>" run airiti-mcp --method tools/call --tool-name airiti_search --tool-arg query="鷹架 數學解題"
```

## Structure (kept ready for a later remote HTTP version)

```
src/airiti_mcp/
  server.py   # MCPServer and tool definitions, independent of the transport
  stdio.py    # ← transport entry point (stdio for now)
  client.py   # HTTP, rate limiting, timeouts, query JSON assembly
  parsers.py  # search-page / detail-page parsing
  hints.py    # suggestions when a search returns few results (variant characters, dropping a keyword)
  cite.py     # APA 7
  cite_export.py  # parsing of Airiti's official citation export (BibTeX / RIS / EndNote)
  search_export.py  # CSV and search-log files for airiti_search_all
  harvest.py  # resumable engine behind airiti_search_all (page fetching, batch export, progress, state.json)
  errors.py
```

To switch to Streamable HTTP, just add one entry file (for example `http.py`: `from .server import mcp; mcp.run("streamable-http")`,
or mount `mcp.streamable_http_app()` on uvicorn and add authentication); nothing from `server.py` down needs to change.
Note: rate limiting is currently a global lock inside a single process; a multi-process deployment needs a shared rate-limiting mechanism.

## Notes

- The MCP Python SDK is now 2.x, and the former `FastMCP` was renamed `MCPServer` (`mcp.server.mcpserver`). 2.x passes only the message of a `ToolError` to the client,
  so `server.py` uses a `_tool` wrapper to turn `AiritiError` into `ToolError`.
- The keys of the query JSON (`SearchFileds` / `SearchFiledList`) use Airiti's original spelling; do not "fix" them. `BSF` is required, and without it there are no results.
- Search field codes (from the enum names in the page's JS): `DSF.FieldName=49` = all fields including full text; `BSF.FieldName=0` = all fields.
- **Theses and dissertations (學位論文)** (DocID starts with `U`, type column "學位論文"): they appear in search results (roughly 30–40%, depending on the topic). Detail pages can be read and citations exported (RIS type `THES`); `airiti_cite_apa` currently supports journal articles only and explicitly rejects them. In a 2026-11 test, all 12 sampled theses could be read and exported; this is Airiti's behavior and may change, and `tests/test_live.py` will detect it.
- **Search matching**: every keyword must match (AND), term by term. In testing, Airiti had already unified 沉/沈, 台/臺, 群/羣, 溫/温, 週/周 and 峰/峯,
  but had **not** unified 啟/啓, 線/綫, 楣/霉, 裡/裏, 衛/衞 and 污/汙; Simplified Chinese input also matches Traditional Chinese records, with slightly fewer results. When a search returns fewer than 5 results, the tool suggests spellings to try.
