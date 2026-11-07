"""Resumable engine behind airiti_search_all.

A run walks through: fetch every result page -> (optionally) export bibliography in batches.
Every step saves `state.json` in the run folder, and the engine stops cleanly at a deadline, so a
call never has to outlive an MCP client's timeout: the caller just calls again with `resume=<folder>`.

A transient error (network, site hiccup) pauses the run instead of ending it; `resume` retries the same
step. After MAX_ERROR_STREAK consecutive failures the engine gives up, keeps what it has and marks the run
incomplete.

The state is plain JSON; nothing here touches the network except through `client`."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import cite_export, client, parsers, search_export
from .errors import AiritiError

PAGE_SIZE = 20
MAX_EXPORT = 20  # tested live with 13 at once; kept modest so one batch stays fast
MAX_ERROR_STREAK = 3  # consecutive failed attempts at the same step before the run gives up
REPAIR_TRIES = 3  # re-fetches of one page boundary before we stop trying to recover a lost record
PAGE_RETRY_WAITS = (3.0, 6.0)  # seconds to wait before the 2nd and 3rd try of a page that came back empty or broken
STATE_FILE = "state.json"
STATE_VERSION = 1  # bump when a field is removed or renamed (new fields with defaults need no bump)
_CRLF2 = "\r\n\r\n"

_now = time.monotonic  # tests replace this with a fake clock
_sleep = asyncio.sleep  # ... and this with a no-op that advances the fake clock
_locks: dict[str, asyncio.Lock] = {}

ProgressFn = Callable[[float, float | None, str], Awaitable[None]]


def now() -> float:
    return _now()


def run_lock(name: str) -> asyncio.Lock:
    """One lock per run folder: two calls must never advance the same run at the same time
    (they would both export the same batch). In-process only; a second MCP server process is not covered."""
    return _locks.setdefault(name, asyncio.Lock())


async def _no_progress(progress: float, total: float | None, message: str) -> None:
    return None


# ------------------------------------------------------------------ shared helpers ----
async def export_ids(ids: list[str], site_type: str, attempts: int = 2) -> tuple[str | None, str]:
    """One export call, retried (the site was seen failing once with an empty result, then succeeding)."""
    site_msg = ""
    for _ in range(attempts):
        body = await client.fetch_cite_export(ids, site_type)
        content = cite_export.parse_cite_export(body)
        if content:
            if site_type == "RIS":  # the only format that carries a UR line; BibTeX / EndNote stay byte for byte
                content = cite_export.absolutize_urls(content, client.BASE)
            return content, ""
        site_msg = cite_export.cite_export_error(body)
    return None, site_msg


def is_thesis(it: parsers.SearchItem) -> bool:
    return "學位論文" in (it.kind or "") or bool(re.match(r"U\d", it.doc_id))


# ------------------------------------------------------------------------- state ----
@dataclass
class RunState:
    query: str
    max_total: int
    export_key: str  # "" = no export
    started: str  # ISO timestamp with timezone
    total: int = 0  # total the site reported
    pages_needed: int = 0
    next_page: int = 2  # page 1 is fetched when the run is created
    pages_fetched: int = 0
    fetched: int = 0  # rows received, duplicates included
    items: list[dict] = field(default_factory=list)  # raw rows while harvesting; unique, capped rows afterwards
    deduped: int = 0  # unique DocIDs before the cap
    harvest_error: str = ""  # set only when the run gave up on harvesting
    harvest_done: bool = False
    ids: list[str] = field(default_factory=list)  # exportable (non-thesis) DocIDs, in order
    skipped_theses: int = 0
    export_cursor: int = 0
    isolate_until: int = 0  # ids[export_cursor:isolate_until] are exported one by one (a batch failed as a whole)
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unattempted: list[str] = field(default_factory=list)
    export_problem: str = ""  # set only when the run gave up on exporting
    export_done: bool = False
    error_streak: int = 0  # consecutive failures at the current step; 0 = healthy
    last_error: str = ""
    calls: int = 0
    elapsed_s: float = 0.0
    finished: bool = False
    export_bytes: int = -1  # size of the export file after the last saved batch; -1 = unknown (older state files)
    version: int = STATE_VERSION
    expected: int = 0  # how many records a complete harvest should hold: min(reported total, cap)
    page_retries: int = 0  # result pages that had to be requested again (empty or broken answers)
    row_page: list[int] = field(default_factory=list)  # page number of every raw row in `items` (while harvesting)
    repair_tries: dict[str, int] = field(default_factory=dict)  # page boundary k (pages k|k+1) -> re-fetch rounds made
    fixed_boundaries: list[int] = field(default_factory=list)  # boundaries confirmed consistent by a fresh fetch
    repaired_records: int = 0  # records recovered by re-fetching boundaries

    def search_items(self) -> list[parsers.SearchItem]:
        return [parsers.SearchItem(**d) for d in self.items]

    def unique_now(self) -> list[dict]:
        """Rows de-duplicated by DocID and cut to the cap (what results.csv shows right now)."""
        seen: set[str] = set()
        out = [d for d in self.items if not (d["doc_id"] in seen or seen.add(d["doc_id"]))]
        return out[: self.max_total]

    @property
    def gave_up(self) -> bool:
        return bool(self.harvest_error or self.export_problem)


def save_state(st: RunState, folder: Path) -> None:
    tmp = folder / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / STATE_FILE)


def load_state(folder: Path) -> RunState:
    try:
        data = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("進度檔不是 JSON 物件")
        if data.get("version", 1) > STATE_VERSION:
            raise ValueError(f"進度檔版本 {data['version']} 比這個程式支援的 {STATE_VERSION} 新，請更新 airiti-mcp 後再續跑")
        return RunState(**data)
    except (OSError, ValueError, TypeError) as e:
        raise AiritiError(f"讀不到這次檢索的進度檔（{folder.name}/{STATE_FILE}）：{e}") from e


def resolve_run_folder(resume: str) -> Path:
    """`resume` is a run-folder NAME under the export root, never a path. The folder must be a direct
    child of the export root once links are resolved (a symlink / junction pointing elsewhere is refused)."""
    name = resume.strip()
    root = search_export.export_root()
    if not name or name in {".", ".."} or "/" in name or "\\" in name or ":" in name:
        raise AiritiError("resume 必須是上次回傳的資料夾名稱（不是路徑）。")
    folder = root / name
    if not (folder / STATE_FILE).is_file():
        raise AiritiError(f"找不到可續跑的檢索「{name}」（在 {root} 底下沒有 {STATE_FILE}）。")
    if folder.resolve().parent != root.resolve():
        raise AiritiError(f"「{name}」不在匯出資料夾裡（可能是連結到別處的捷徑），拒絕續跑。")
    return folder


async def fetch_search_page(
    query: str, page: int, page_size: int, *, waits: tuple[float, ...] = PAGE_RETRY_WAITS, deadline: float | None = None
) -> tuple[parsers.SearchPage | None, AiritiError | None, int, bool]:
    """One result page, asked again when it comes back empty or broken.

    Airiti sometimes answers a VALID page with the same "0 筆" block a truly empty query gets (measured about
    2% of page requests, clustered in time), so an empty answer is not believed until it repeats.
    Returns (page or None, last error or None, retries made, exhausted). `exhausted` is False when the
    deadline left no time for the remaining retries - the caller should pause, not blame the site."""
    res: parsers.SearchPage | None = None
    err: AiritiError | None = None
    used = 0
    for attempt in range(len(waits) + 1):
        if attempt:
            wait = waits[attempt - 1]
            if deadline is not None and _now() + wait >= deadline:
                return res, err, used, False
            await _sleep(wait)
            used += 1
        try:
            res, err = parsers.parse_search(await client.fetch_search_html(query, page, page_size)), None
        except AiritiError as e:
            res, err = None, e
            continue
        if res.items:
            return res, None, used, True
    return res, err, used, True


async def start_run(query: str, max_total: int, export_key: str) -> tuple[RunState | None, str]:
    """Fetch page 1. Returns (state, "") or (None, hint-text) when the site keeps reporting 0 results."""
    from . import hints

    first, err, used, _ = await fetch_search_page(query, 1, PAGE_SIZE)
    if first is None:
        raise err  # type: ignore[misc]  # res is None only when every try raised
    if first.total == 0:
        return None, f"已重試 {used} 次，華藝仍回報 0 筆（華藝偶爾會對有效的查詢暫時回 0 筆，所以才重試）。\n" + hints.few_results_hint(query, 0)
    st = RunState(
        query=query, max_total=max_total, export_key=export_key,
        started=datetime.now().astimezone().isoformat(timespec="seconds"),
        total=first.total, pages_needed=-(-min(first.total, max_total) // PAGE_SIZE),
        pages_fetched=1, fetched=len(first.items), items=[asdict(i) for i in first.items], page_retries=used,
        row_page=[1] * len(first.items),
    )
    return st, ""


# ------------------------------------------------------------------------ engine ----
def _units_total(st: RunState) -> float:
    return st.pages_needed * (2 if st.export_key else 1)


def _record_error(st: RunState, where: str, e: AiritiError) -> bool:
    """Note a failed attempt. Returns True when the run should give up (too many in a row)."""
    st.error_streak += 1
    st.last_error = f"{where}：{type(e).__name__}: {e}"
    return st.error_streak >= MAX_ERROR_STREAK


def _healthy(st: RunState) -> None:
    st.error_streak = 0
    st.last_error = ""


def _overlapping_boundaries(st: RunState) -> list[int]:
    """Pages k where page k and page k+1 both hold the same DocID."""
    pages: dict[int, set[str]] = {}
    for d, pg in zip(st.items, st.row_page):
        pages.setdefault(pg, set()).add(d["doc_id"])
    return [k for k in sorted(pages) if k + 1 in pages and pages[k] & pages[k + 1]]


async def _repair_boundaries(st: RunState, folder: Path, deadline: float) -> bool:
    """Recover records lost to Airiti's unstable tie order. Records with equal rank come back in a random order
    on EVERY request, and each page is its own request, so a tie group straddling a page boundary can show the
    same record at the end of page k and the start of page k+1 and leave its twin out entirely (seen live:
    282 rows, 281 distinct, 1 missing). A repeated DocID across a boundary therefore always means a lost one.
    Fix: ask for the two pages again; a fresh pair that shares nothing contains the whole tie group, and any
    record we did not have is added. Best effort, REPAIR_TRIES rounds per boundary. Returns False if paused."""
    if len(st.row_page) != len(st.items):  # state from before row_page existed: cannot tell which page a row came from
        return True
    for k in _overlapping_boundaries(st):
        while st.repair_tries.get(str(k), 0) < REPAIR_TRIES and k not in st.fixed_boundaries:
            if _now() >= deadline:
                return False
            fresh: dict[int, set[str]] = {}
            for pg in (k, k + 1):
                res, _err, used, exhausted = await fetch_search_page(st.query, pg, PAGE_SIZE, deadline=deadline)
                st.page_retries += used
                if res is None or not res.items:
                    if not exhausted:
                        return False  # out of time, not out of luck: do not count this round
                    break  # the site would not answer: this round is lost, try again next round
                known = {d["doc_id"] for d in st.items}
                fresh[pg] = {i.doc_id for i in res.items}
                for it in res.items:
                    if it.doc_id not in known:
                        st.items.append(asdict(it))
                        st.row_page.append(pg)
                        st.repaired_records += 1
                        st.fetched += 1  # a row we now hold, so the dedupe line adds up
            st.repair_tries[str(k)] = st.repair_tries.get(str(k), 0) + 1
            if len(fresh) == 2 and not (fresh[k] & fresh[k + 1]):
                st.fixed_boundaries.append(k)
            save_state(st, folder)
    return True


async def _harvest(st: RunState, folder: Path, deadline: float, progress: ProgressFn) -> None:
    while st.next_page <= st.pages_needed:
        if _now() >= deadline:
            return  # paused: harvest_done stays False
        page = st.next_page
        res, err, used, exhausted = await fetch_search_page(st.query, page, PAGE_SIZE, deadline=deadline)
        st.page_retries += used
        if res is None or not res.items:
            if not exhausted:  # no time left for the retries: pause without blaming the site
                save_state(st, folder)
                return
            failure = err or AiritiError(
                f"華藝對第 {page} 頁回報 0 筆（頁碼在 1–{st.pages_needed} 之內，多半是暫時性的空頁；已重試 {used} 次）"
            )
            if not _record_error(st, f"第 {page} 頁", failure):
                save_state(st, folder)
                return  # paused for a retry on the next call
            st.harvest_error = st.last_error  # gave up: keep what we have; the log says the run is incomplete
            break
        _healthy(st)
        st.pages_fetched += 1
        st.fetched += len(res.items)
        st.items += [asdict(i) for i in res.items]
        st.row_page += [page] * len(res.items)
        st.next_page += 1
        save_state(st, folder)
        await progress(st.pages_fetched, _units_total(st), f"已抓 {st.pages_fetched}/{st.pages_needed} 頁")
    if not st.harvest_error and not await _repair_boundaries(st, folder, deadline):
        return  # paused while repairing; resume continues from the saved tries
    if len(st.row_page) == len(st.items):  # recovered records were appended: put them back on their page
        order = sorted(range(len(st.items)), key=lambda i: st.row_page[i])  # stable: keeps the site's order per page
        st.items = [st.items[i] for i in order]
    st.row_page = []
    # harvest over (all pages, or gave up): de-duplicate and apply the cap
    seen: set[str] = set()
    deduped = [d for d in st.items if not (d["doc_id"] in seen or seen.add(d["doc_id"]))]
    st.deduped = len(deduped)
    st.expected = min(st.total, st.max_total)
    st.items = deduped[: st.max_total]
    items = st.search_items()
    st.ids = [it.doc_id for it in items if not is_thesis(it)]
    st.skipped_theses = len(items) - len(st.ids)
    st.harvest_done = True
    st.export_bytes = 0  # the export file starts empty from here
    if not st.export_key or not st.ids:
        st.export_done = True
    save_state(st, folder)


async def _export(st: RunState, folder: Path, deadline: float, progress: ProgressFn) -> None:
    """One unit = one batch of up to MAX_EXPORT ids, or a single id while isolating a bad one. The deadline is
    checked between units, so even isolating a batch with an invalid DocID can pause and resume."""
    site_type, ext, _ = cite_export.FORMATS[st.export_key]
    path = folder / f"export.{ext}"
    if st.export_bytes >= 0 and path.exists() and path.stat().st_size > st.export_bytes:
        # an earlier call wrote a batch but died before saving state (killed, power loss): drop the unsaved
        # tail so that batch is exported again exactly once. -1 (older state files) never truncates.
        with path.open("r+b") as f:
            f.truncate(st.export_bytes)
    while st.export_cursor < len(st.ids):
        if _now() >= deadline:
            return  # paused
        cursor = st.export_cursor
        single = cursor < st.isolate_until
        batch = st.ids[cursor : cursor + (1 if single else MAX_EXPORT)]
        try:
            content, _ = await export_ids(batch, site_type, attempts=1 if single else 2)
        except AiritiError as e:  # network/parse trouble
            if not _record_error(st, "匯出", e):
                save_state(st, folder)
                return  # paused for a retry on the next call
            handled = set(st.done) | set(st.failed)
            st.unattempted = [i for i in st.ids[cursor:] if i not in handled]
            st.export_problem = st.last_error
            st.export_done = True
            save_state(st, folder)
            return
        _healthy(st)
        if content:
            with path.open("a", encoding="utf-8", newline="") as f:
                f.write(content + _CRLF2)
            st.export_bytes = path.stat().st_size
            st.done += batch
        elif len(batch) > 1:  # one invalid DocID fails the whole batch: take it apart, one id per unit
            st.isolate_until = cursor + len(batch)
            save_state(st, folder)
            continue
        else:  # a single id the site refuses
            st.failed += batch
        st.export_cursor += len(batch)
        save_state(st, folder)
        await progress(
            st.pages_needed + st.pages_needed * st.export_cursor / len(st.ids), _units_total(st),
            f"已匯出 {st.export_cursor}/{len(st.ids)} 篇",
        )
    st.export_done = True
    save_state(st, folder)


async def advance(
    st: RunState, folder: Path, budget_s: float, progress: ProgressFn | None = None, spent_s: float = 0.0
) -> bool:
    """Work until finished or the budget is used up. `spent_s` is time this call already used (fetching
    page 1 before the run existed). Returns True when the run is complete. The deadline is checked between
    units (one page, one export batch, one single id), so a call overshoots by at most one unit."""
    raw_progress = progress or _no_progress
    last = [-1.0]

    async def progress(done: float, total: float | None, message: str) -> None:
        if done > last[0]:  # MCP requires progress to increase with every notification
            last[0] = done
            await raw_progress(done, total, message)

    t0 = _now()
    deadline = t0 + max(budget_s - spent_s, 0.0)
    try:
        if not st.harvest_done:
            await _harvest(st, folder, deadline, progress)
        if st.harvest_done and not st.export_done:
            await _export(st, folder, deadline, progress)
    finally:
        st.calls += 1
        st.elapsed_s += (_now() - t0) + spent_s
        save_state(st, folder)
    done = st.harvest_done and st.export_done
    if done:
        write_final_files(st, folder)
        if not st.gave_up:  # an abandoned run must not look finished in a progress bar
            await progress(_units_total(st), _units_total(st), "完成")
    else:
        write_partial_csv(st, folder)
    return done


# ----------------------------------------------------------------------- outputs ----
def _write_csv(items: list[parsers.SearchItem], folder: Path) -> Path:
    path = folder / "results.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel reads UTF-8
        f.write(search_export.build_csv(items))
    return path


def write_partial_csv(st: RunState, folder: Path) -> None:
    _write_csv([parsers.SearchItem(**d) for d in st.unique_now()], folder)


def export_lines(st: RunState) -> list[str]:
    if not st.export_key:
        return []
    _, ext, desc = cite_export.FORMATS[st.export_key]
    name = f"export.{ext}"
    lines = [
        f"書目匯出：{desc}，成功 {len(st.done)} 篇 → {name if st.done else '（未產生檔案）'}",
        f"  略過學位論文 {st.skipped_theses} 篇（無法匯出）",
    ]
    if st.export_key == "ris" and st.done:
        lines.append("  備註：沒有 DOI 的記錄，華藝給的 UR 是相對路徑，已補成完整網址；其餘內容原樣。")
    if st.failed:
        shown = "、".join(st.failed[:20]) + (f"…（另 {len(st.failed) - 20} 篇）" if len(st.failed) > 20 else "")
        lines.append(f"  【匯出失敗】{len(st.failed)} 篇（華藝拒絕這些 DocID）：{shown}")
    if st.unattempted:
        lines.append(f"  【因中斷未處理】{len(st.unattempted)} 篇（沒有發出匯出請求，不代表 DocID 無效）")
    if st.export_problem:
        lines.append(f"  【匯出中斷】連續失敗 {MAX_ERROR_STREAK} 次後放棄：{st.export_problem}")
    return lines


def repair_lines(st: RunState) -> list[str]:
    if not st.repair_tries:
        return []
    unconfirmed = len(st.repair_tries) - len(st.fixed_boundaries)
    line = (
        f"邊界修補：{len(st.repair_tries)} 處頁邊界出現重複（排名相同的記錄在兩次請求間順序互換），"
        f"重抓相鄰兩頁後補回 {st.repaired_records} 筆"
    )
    if unconfirmed:
        line += f"；{unconfirmed} 處在 {REPAIR_TRIES} 輪內沒有得到一致的結果，可能仍缺記錄"
    return [line]


def write_final_files(st: RunState, folder: Path) -> None:
    items = st.search_items()
    _write_csv(items, folder)
    when = datetime.fromisoformat(st.started)
    note = f"執行方式：分 {st.calls} 次呼叫完成，累計約 {st.elapsed_s:.0f} 秒" if st.calls > 1 else ""
    harvest_error = f"連續失敗 {MAX_ERROR_STREAK} 次後放棄：{st.harvest_error}" if st.harvest_error else ""
    (folder / "search_log.txt").write_text(
        search_export.build_log(
            query=st.query, when=when, reported_total=st.total, fetched=st.fetched, deduped=st.deduped,
            unique=len(items), pages_fetched=st.pages_fetched, page_size=PAGE_SIZE, cap=st.max_total,
            items=items, error=harvest_error, export_lines=export_lines(st), run_note=note,
            expected=st.expected, page_retries=st.page_retries, notes=repair_lines(st),
        ),
        encoding="utf-8",
    )
    st.finished = True
    save_state(st, folder)


def summary(st: RunState, folder: Path, budget_s: float | None = None) -> str:
    n_unique = len(st.items) if st.harvest_done else len(st.unique_now())
    if st.finished:
        head = f"查詢「{st.query}」：華藝回報共 {st.total} 筆；取得 {n_unique} 筆（{st.pages_fetched} 頁）。"
    else:
        where = (
            f"抓取結果頁 {st.pages_fetched}/{st.pages_needed}"
            if not st.harvest_done
            else f"匯出書目 {st.export_cursor}/{len(st.ids)} 篇"
        )
        if st.error_streak:
            head = (
                f"【因錯誤暫停，尚未完成】{st.last_error}（連續第 {st.error_streak}/{MAX_ERROR_STREAK} 次失敗）；進度：{where}。\n"
                f"請稍候再呼叫 airiti_search_all(resume=\"{folder.name}\") 重試；"
                f"連續失敗 {MAX_ERROR_STREAK} 次會放棄，並把已取得的部分標示為不完整。"
            )
        else:
            head = (
                f"【已暫停，尚未完成】時間預算（{budget_s:.0f} 秒）用完；進度：{where}。\n"
                f"請再呼叫一次 airiti_search_all(resume=\"{folder.name}\") 繼續，其他參數不用重填；"
                "重複呼叫直到不再出現【已暫停】為止。"
            )
        head += f"\n查詢「{st.query}」：華藝回報共 {st.total} 筆。"
    out = [head, f"資料夾：{folder}"]
    if st.finished:
        out += [f"清單：{folder / 'results.csv'}", f"檢索紀錄：{folder / 'search_log.txt'}"]
        if st.export_key:
            ext = cite_export.FORMATS[st.export_key][1]
            out.append(f"書目檔：{folder / ('export.' + ext)}" if st.done else "書目檔：（未產生）")
            out += [l.strip() for l in export_lines(st)]
        if st.harvest_error:
            out.append(f"【不完整】抓取中途失敗，已存下已取得的部分。{st.harvest_error}")
        if st.total > st.max_total:
            out.append(
                f"【截斷】總筆數 {st.total} 超過 max_total={st.max_total}；"
                "建議加關鍵字縮小範圍，或調高 max_total。"
            )
        out += repair_lines(st)
        missing = max(0, st.expected - st.deduped)
        if missing and not st.harvest_error:
            out.append(f"【完整性】預期 {st.expected} 筆，只取得 {st.deduped} 筆（缺 {missing} 筆）；原因見檢索紀錄。")
        dups = search_export.duplicate_groups(st.search_items())
        if dups:
            out.append(f"疑似重複 {len(set(dups.values()))} 組、涉及 {len(dups)} 筆（篇名與作者相同、DocID 不同；未移除，見 results.csv「疑似重複組」欄與檢索紀錄）。")
        shown = st.search_items()[:5]
        out += ["", "前 5 筆："] + [f"{i}. {it.title}（DocID：{it.doc_id}）" for i, it in enumerate(shown, 1)]
    else:
        out.append(f"目前清單（暫存）：{folder / 'results.csv'}")
    return "\n".join(out)
