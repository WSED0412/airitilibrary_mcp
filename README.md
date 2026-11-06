# airiti-mcp

華藝線上圖書館（Airiti Library）的本機 MCP server（stdio）。**只做檢索與讀摘要**，不下載全文。

| Tool | 用途 |
|---|---|
| `airiti_search(query, page=1, max_results=10)` | 搜尋（所有欄位）。回傳總筆數＋本頁每筆的篇名、作者、出處、DocID、詳目頁網址。`max_results` 1–20；每次只抓一頁。 |
| `airiti_abstract(doc_id, include_references=False, include_cited_by=False)` | 完整書目（含 DOI、英文篇名、語言、OpenAccess、可全文下載標記）、中文摘要、英文並列摘要、中英關鍵字。選填：`include_references` 附參考文獻、`include_cited_by` 附華藝收錄的被引用記錄（兩者預設關閉，清單可能很長；有 DocID 的可再丟給本工具）。 |
| `airiti_cite_apa(doc_id)` | 以書目資料產生 APA 7 引用（目前只支援期刊論文；學位論文會明確拒絕）。 |
| `airiti_export_citation(doc_ids, format="bibtex", save=False)` | 回傳華藝官方匯出的書目檔內容，`doc_ids` 為清單或單一字串（一次最多 20 篇，合成一個檔），`format` 為 `bibtex`／`ris`／`endnote`，可存檔後匯入 Zotero、Mendeley、EndNote 等。內容來自華藝，**只做一項修改**：RIS 裡沒有 DOI 的記錄，華藝給的 `UR` 是相對路徑（`Article/Detail?docID=…`，匯入後連結會壞），這裡補成完整網址；其餘原樣。不論幾篇都只多 2 個請求。`save=True` 時不把全文放進回傳（20 篇約 3–4 萬字元，會吃掉大量對話上下文），改存成匯出資料夾下新的子資料夾裡的 `export.<副檔名>`，只回傳路徑。注意：清單中只要有一個無效 DocID，華藝會整批失敗。 |
| `airiti_search_all(query, max_total=1000, export_format="", resume="", time_budget_s=90)` | 抓完一個查詢的所有結果頁（每頁 20 筆、頁間隔 1.5 秒），寫成 `results.csv`（DocID、篇名、作者、出處、類型…，另有「預刊」欄與「疑似重複組」欄：華藝會把同一篇文章的預刊版與正式刊出版各收一筆，篇名（全形半形視為相同）＋作者相同但 DocID 不同的記錄會標成同一組 D1、D2…（沒有作者的記錄不分組），**只標記、不移除**，保留哪一筆由你判斷）與 `search_log.txt`（檢索日期、檢索式、總筆數、去重與截斷各移除幾筆、疑似重複組數、類型分布，可寫進方法章節）。檔案存到 `~/airiti_exports/<時間>_<關鍵字>/`，可用環境變數 `AIRITI_EXPORT_DIR` 改位置；工具本身不接受路徑。`max_total` 1–2000。選填 `export_format`（`bibtex`／`ris`／`endnote`）：把可匯出的文獻（學位論文除外）每 20 篇一批向華藝匯出，合併成同資料夾的 `export.bib`／`.ris`／`.enw`；某批失敗會逐篇重試找出無效 DocID，並記在檢索紀錄。**逾時保護**：單次呼叫最多工作約 `time_budget_s` 秒（10–900）就乾淨暫停並把進度存成 `state.json`，回傳【已暫停】與資料夾名稱，再用 `resume=<資料夾名稱>` 呼叫即可從斷點繼續（不會重抓、不會重複匯出）；重複呼叫到不再暫停為止。暫時性錯誤（網路等）不會直接結束檢索：會【因錯誤暫停】，`resume` 重試同一步，連續失敗 3 次才放棄並把已取得的部分標示為不完整；同一個檢索不能同時被兩個呼叫推進（第二個會被拒絕，避免重複匯出）；`resume` 只認匯出資料夾裡的資料夾名稱，指到別處的連結會被拒絕。有進度回報（MCP progress）。只回傳摘要與路徑。 |

## 行為與限制

- 不碰任何全文／PDF 下載端點；**不做任何快取**（頁面標示 `noarchive`），每次呼叫即時查詢。
- 任兩次請求至少間隔 1.5 秒；逾時 15 秒。
- 錯誤分開回報：`AiritiNetworkError`（連不上／逾時／非 200）、`AiritiParseError`（頁面結構不符，例如改版或 DocID 不存在）、
  以及「0 筆結果」（連線與解析皆正常，確實沒有符合的文獻）。解析不到的欄位會寫「（無）」並在備註說明，不會猜。
- DocID 格式不固定（可能含 `+` 等字元），一律原樣使用，由 search 結果取得。

## 安裝

需要 [uv](https://docs.astral.sh/uv/)（`winget install astral-sh.uv`）。

```powershell
cd <本專案資料夾>
uv sync
uv run pytest                      # 離線測試
$env:AIRITI_LIVE=1; uv run pytest tests/test_live.py   # 實網測試（約 5 次請求）
```

## 註冊到 Claude Code

```powershell
claude mcp add airiti -- uv --directory "<專案資料夾完整路徑>" run airiti-mcp
```

加 `--scope user` 可在所有專案使用。用 `claude mcp list` 確認。

## 註冊到 Claude 桌面版

編輯 `claude_desktop_config.json`（設定 → 開發人員 → 編輯設定可直接開啟），加入下列內容。一般安裝在 `%APPDATA%\Claude\`；
商店版（MSIX）在 `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`。JSON 裡路徑的每個 `\` 都要寫成 `\\`（例如 `C:\\Users\\你\\...`）。

```json
{
  "mcpServers": {
    "airiti": {
      "command": "<uv.exe完整路徑>",
      "args": [
        "--directory",
        "<專案資料夾完整路徑>",
        "run",
        "airiti-mcp"
      ]
    }
  }
}
```

存檔後完全結束並重開 Claude 桌面版。桌面版不一定繼承 PATH，所以 `command` 用 uv 的絕對路徑（`where uv` 可查）。
若專案路徑含中文導致啟動失敗，把專案移到純英文路徑（如 `C:\mcp\airiti`）再改 `--directory`。

## 用 MCP Inspector 測試

```powershell
# 互動式介面
npx @modelcontextprotocol/inspector uv --directory "<專案路徑>" run airiti-mcp
# 指令列
npx @modelcontextprotocol/inspector --cli uv --directory "<專案路徑>" run airiti-mcp --method tools/list
npx @modelcontextprotocol/inspector --cli uv --directory "<專案路徑>" run airiti-mcp --method tools/call --tool-name airiti_search --tool-arg query="鷹架 數學解題"
```

## 結構（為了之後換成 HTTP 遠端版）

```
src/airiti_mcp/
  server.py   # MCPServer 與 tool 定義，與傳輸層無關
  stdio.py    # ← 傳輸層入口（目前 stdio）
  client.py   # HTTP、限速、逾時、查詢 JSON 組裝
  parsers.py  # 搜尋頁／詳目頁解析
  cite.py     # APA 7
  cite_export.py  # 華藝官方書目匯出（BibTeX／RIS／EndNote）的解析
  search_export.py  # airiti_search_all 的 CSV 與檢索紀錄檔
  harvest.py  # airiti_search_all 的可續跑引擎（抓頁、批次匯出、進度、state.json）
  errors.py
```

改 Streamable HTTP 時只新增一個入口檔（例如 `http.py`：`from .server import mcp; mcp.run("streamable-http")`，
或用 `mcp.streamable_http_app()` 掛到 uvicorn 並加上驗證），`server.py` 以下完全不用動。
注意：限速目前是單一行程內的全域鎖；多行程部署時要改成共用的限速機制。

## 備註

- MCP Python SDK 已是 2.x，原本的 `FastMCP` 改名為 `MCPServer`（`mcp.server.mcpserver`）。2.x 只會把 `ToolError` 的訊息傳給用戶端，
  所以 `server.py` 用 `_tool` 包裝把 `AiritiError` 轉成 `ToolError`。
- 查詢 JSON 的 key（`SearchFileds`／`SearchFiledList`）是華藝原本的拼字，請勿修正。`BSF` 必填，沒送就不會有結果。
- 搜尋欄位代碼（來自頁面 JS 列舉名稱）：`DSF.FieldName=49` ＝ 所有欄位含全文；`BSF.FieldName=0` ＝ 所有欄位。
- **學位論文**：華藝已把學位論文分到另一個網站（airitietds.com），`airitilibrary.com` 的搜尋結果只剩期刊／會議論文，
  `U…` 開頭的學位論文 DocID（常出現在被引用記錄裡）也讀不到詳目頁，工具會明說原因。
- **搜尋比對**：每個關鍵字都必須符合（AND），逐詞比對。實測華藝已統一 沉/沈、台/臺、群/羣、溫/温、週/周、峰/峯，
  但**沒統一** 啟/啓、線/綫、楣/霉、裡/裏、衛/衞、污/汙；簡體輸入有互通但筆數略少。結果少於 5 筆時，工具會提示可試的寫法。
