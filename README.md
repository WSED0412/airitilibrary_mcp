# airiti-mcp

華藝線上圖書館（Airiti Library）的本機 MCP server（stdio）。**只做檢索與讀摘要**，不下載全文。

> ⚠️ **使用前請先讀下方〈使用規範與風險自負〉。** 華藝的《著作權聲明暨網站使用規範》對「自動化程式」的使用有限制；本專案是否涵蓋在內，作者無法確定。**使用風險由你自行承擔。**

## 使用規範與風險自負（請先讀）

本專案會由程式代為向華藝線上圖書館送出查詢（搜尋、讀摘要、匯出書目）。華藝公布的
**[《著作權聲明暨網站使用規範》](https://www.airitilibrary.com/Home/Editor)** 對這類使用有限制，請在使用前自行閱讀原文。

**第四條原文**（2026-11-07 讀取；條款可能更新，請以官網最新版本為準）：

> 本網站亦禁止使用人透過自動化程式執行查詢、下載、撈取資料、擷取內容…等方式，使用本網站或將本網站收錄之內容另行載入自行或他人開發之系統或資料庫中；且使用者不得將本網站收錄之資料置入自己或第三方之自動化學習系統、數據訓練平台，或提供第三方進行AI相關數據處理與開發。

第一條則只允許「個人、非商業用途」，且是「透過瀏覽器、手機等各類載具」進行查詢、檢閱、列印、下載。

**作者的疑慮（我們不確定，也不下定論）：**

- 本專案算不算「透過自動化程式執行查詢、下載、撈取資料、擷取內容」，**作者無法確定**。由使用者發起的單次查詢有解讀空間；整批檢索與批次匯出（`airiti_search_all`）則很難說不是「撈取資料」。
- 把查詢結果（摘要、書目）交給 AI 助理處理，算不算條文所稱「提供第三方進行 AI 相關數據處理」，同樣不確定。
- 作者不是律師，這裡**不是法律意見**。是否違反由華藝（以及你所屬機構與華藝之間的授權合約）認定。
- 華藝的 [`robots.txt`](https://www.airitilibrary.com/robots.txt) 另外點名封鎖多種 AI 爬蟲，並對一般爬蟲列出 `Disallow: /Article/Query/`（搜尋功能）。

**風險自負：** 使用本專案（含其程式碼、文件與任何輸出）的一切風險由使用者自行承擔。作者不保證它符合華藝的使用規範，
也**不對任何後果負責**，包括但不限於帳號或 IP 被限制、機構授權受影響，以及法律責任。

**請遵守以下限制：**

1. **不要用本 MCP 將華藝網站的內容擷取後，另行載入自己或他人開發的系統或資料庫中**（華藝第四條；包含你自己的資料庫、知識庫、向量資料庫，以及他人開發的平台）。
2. **不要把擷取到的內容用於訓練或微調模型、置入數據訓練平台，或提供第三方做 AI 資料開發。**
3. 僅供**個人、非商業**的研究參考；不得重製、散布、公開傳輸，或做任何商業使用（第一、二條）。
4. 有疑慮時，請先詢問華藝或你所屬學校的圖書館。

本專案設計了限速（任兩次請求至少間隔 1.5 秒）、誠實的 User-Agent、不碰全文、不做快取；這些**只是降低站方負擔的做法，不等於取得華藝的許可**。

| Tool | 用途 |
|---|---|
| `airiti_search(query, page=1, max_results=10)` | 搜尋（所有欄位）。回傳總筆數＋本頁每筆的篇名、作者、出處、DocID、詳目頁網址。`max_results` 1–20；每次只抓一頁。 |
| `airiti_abstract(doc_id, include_references=False, include_cited_by=False)` | 完整書目（含 DOI、英文篇名、語言、OpenAccess、可全文下載標記）、中文摘要、英文並列摘要、中英關鍵字。選填：`include_references` 附參考文獻、`include_cited_by` 附華藝收錄的被引用記錄（兩者預設關閉，清單可能很長；有 DocID 的可再丟給本工具）。沒有卷號的期刊（頁面上的期號常被華藝截掉前幾位）會多發 2 個請求，改用官方匯出資料的期號；學位論文沒有期號，不會多發。 |
| `airiti_cite_apa(doc_id)` | 以書目資料產生中文 APA 7 引用。**只支援中文期刊論文**（篇名與所有作者姓名皆為中文）；學位論文與英文／外文論文會明確拒絕，並指引改用 `airiti_export_citation` 匯出 RIS／BibTeX 到書目軟體排版。沒有卷號的文獻，頁面上的期號常被華藝截掉前幾位（實測 9 篇全錯），所以會改用官方匯出資料的期號（多 2 個請求；核對失敗就不列期號）。 |
| `airiti_export_citation(doc_ids, format="bibtex", save=False)` | 回傳華藝官方匯出的書目檔內容，`doc_ids` 為清單或單一字串（一次最多 20 篇，合成一個檔），`format` 為 `bibtex`／`ris`／`endnote`，可存檔後匯入 Zotero、Mendeley、EndNote 等。內容來自華藝，**只做一項修改**：RIS 裡沒有 DOI 的記錄，華藝給的 `UR` 是相對路徑（`Article/Detail?docID=…`，匯入後連結會壞），這裡補成完整網址；其餘原樣。不論幾篇都只需 2 個請求（站方偶爾回空時會重試，最多 4 個）。內容超過 3 萬字元（約 15 篇以上）會**自動**存成匯出資料夾下新的子資料夾裡的 `export.<副檔名>`，只回傳路徑，免得佔滿對話上下文；`save=True` 則一律存檔。注意：清單中只要有一個無效 DocID，華藝會整批失敗。 |
| `airiti_search_all(query, max_total=1000, export_format="", resume="", time_budget_s=90)` | 抓完一個查詢的所有結果頁（每頁 20 筆、頁間隔 1.5 秒），寫成 `results.csv`（DocID、篇名、作者、出處、類型…，另有「預刊」欄與「疑似重複組」欄：華藝會把同一篇文章的預刊版與正式刊出版各收一筆，篇名（全形半形視為相同）＋作者相同但 DocID 不同的記錄會標成同一組 D1、D2…（沒有作者的記錄不分組），**只標記、不移除**，保留哪一筆由你判斷）與 `search_log.txt`（檢索日期、檢索式、總筆數、去重與截斷各移除幾筆、疑似重複組數、類型分布，可寫進方法章節）。檔案存到 `~/airiti_exports/<時間>_<關鍵字>/`，可用環境變數 `AIRITI_EXPORT_DIR` 改位置；工具本身不接受路徑。`max_total` 1–2000。選填 `export_format`（`bibtex`／`ris`／`endnote`）：把結果（含學位論文）每 20 篇一批向華藝匯出，合併成同資料夾的 `export.bib`／`.ris`／`.enw`；某批失敗會逐篇重試找出無效 DocID，並記在檢索紀錄。**逾時保護**：單次呼叫最多工作約 `time_budget_s` 秒（10–900）就乾淨暫停並把進度存成 `state.json`，回傳【已暫停】與資料夾名稱，再用 `resume=<資料夾名稱>` 呼叫即可從斷點繼續（不會重抓、不會重複匯出）；重複呼叫到不再暫停為止。暫時性錯誤（網路等）不會直接結束檢索：會【因錯誤暫停】，`resume` 重試同一步，連續失敗 3 次才放棄並把已取得的部分標示為不完整；同一個檢索不能同時被兩個呼叫推進（第二個會被拒絕，避免重複匯出）；`resume` 只認匯出資料夾裡的資料夾名稱，指到別處的連結會被拒絕。有進度回報（MCP progress）。**分頁可靠性**（實測華藝的行為）：對有效頁碼偶爾會回「0 筆」（約 2%），一頁回空或出錯會自動等 3 秒、6 秒重試；排名相同的記錄在每次請求都隨機排序，橫跨頁邊界時會出現「一筆重複、一筆漏抓」，偵測到相鄰兩頁有重複時會重抓這兩頁補回（每個邊界最多 3 輪，補回的記錄依原本名次排回去）；紀錄檔有「完整性核對」（預期筆數 vs 實際不重複筆數，缺幾筆會寫出來，修補連線失敗連續 3 次會中止並註明）。第 1 頁回 0 筆也會先確認再說「查無結果」。詳目頁也有同類現象（有效的 DocID 偶爾回首頁殼，約 3%），`airiti_abstract`／`airiti_cite_apa` 遇到會先重試再說「DocID 不存在」。只回傳摘要與路徑。 |

## 行為與限制

- 不碰任何全文／PDF 下載端點；**不做任何快取**（頁面標示 `noarchive`），每次呼叫即時查詢。
- 任兩次請求至少間隔 1.5 秒；逾時 15 秒。
- 以上只是降低站方負擔的設計，**不代表符合華藝的使用規範**，見最上方〈使用規範與風險自負〉。
- 錯誤分開回報：`AiritiNetworkError`（連不上／逾時／非 200）、`AiritiParseError`（頁面結構不符，例如改版或 DocID 不存在）、
  以及「0 筆結果」（連線與解析皆正常，確實沒有符合的文獻）。解析不到的欄位會寫「（無）」並在備註說明，不會猜。
- DocID 格式不固定（可能含 `+` 等字元），一律原樣使用，由 search 結果取得。

## 安裝

需要 [uv](https://docs.astral.sh/uv/)（`winget install astral-sh.uv`）。

```powershell
cd <本專案資料夾>
uv sync
uv run pytest                      # 離線測試（不連網）
$env:AIRITI_LIVE=1; uv run pytest tests/test_live.py   # 實網測試（約 45 個請求、1 分多鐘；請求間隔 1.5 秒）
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
  hints.py    # 搜尋結果少時的建議（異體字、少一個關鍵字）
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
- **學位論文**（DocID 以 `U` 開頭、類型欄為「學位論文」）：搜尋結果裡有（約三到四成，視主題而定）。詳目頁讀得到、書目也匯得出來（RIS 類型為 `THES`）；`airiti_cite_apa` 目前只支援期刊論文，學位論文會明確拒絕。2026-11 實測抽樣 12 篇全部可讀可匯出；這是華藝的行為，將來可能再變，`tests/test_live.py` 會偵測。
- **搜尋比對**：每個關鍵字都必須符合（AND），逐詞比對。實測華藝已統一 沉/沈、台/臺、群/羣、溫/温、週/周、峰/峯，
  但**沒統一** 啟/啓、線/綫、楣/霉、裡/裏、衛/衞、污/汙；簡體輸入有互通但筆數略少。結果少於 5 筆時，工具會提示可試的寫法。
