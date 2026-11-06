"""Resumable engine behind airiti_search_all.

A run walks through: fetch every result page -> (optionally) export bibliography in batches.
Every step saves `state.json` in the run folder, and the engine stops cleanly at a deadline, so a
call never has to outlive an MCP client's timeout: the caller just calls again with `resume=<folder>`.

The state is plain JSON; nothing here touches the network except through `client`."""
from __future__ import annotations

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
STATE_FILE = "state.json"
_CRLF2 = "\r\n\r\n"

_now = time.monotonic  # tests replace this with a fake clock

ProgressFn = Callable[[float, float | None, str], Awaitable[None]]


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
    harvest_error: str = ""
    harvest_done: bool = False
    ids: list[str] = field(default_factory=list)  # exportable (non-thesis) DocIDs, in order
    skipped_theses: int = 0
    export_cursor: int = 0
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unattempted: list[str] = field(default_factory=list)
    export_problem: str = ""
    export_done: bool = False
    calls: int = 0
    elapsed_s: float = 0.0
    finished: bool = False

    def search_items(self) -> list[parsers.SearchItem]:
        return [parsers.SearchItem(**d) for d in self.items]

    def unique_now(self) -> list[dict]:
        """Rows de-duplicated by DocID and cut to the cap (what results.csv shows right now)."""
        seen: set[str] = set()
        out = [d for d in self.items if not (d["doc_id"] in seen or seen.add(d["doc_id"]))]
        return out[: self.max_total]


def save_state(st: RunState, folder: Path) -> None:
    tmp = folder / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / STATE_FILE)


def load_state(folder: Path) -> RunState:
    try:
        return RunState(**json.loads((folder / STATE_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as e:
        raise AiritiError(f"讀不到這次檢索的進度檔（{folder.name}/{STATE_FILE}）：{e}") from e


def resolve_run_folder(resume: str) -> Path:
    """`resume` is a run-folder NAME under the export root, never a path."""
    name = resume.strip()
    root = search_export.export_root()
    folder = root / name
    if not name or name in {".", ".."} or "/" in name or "\\" in name or ":" in name:
        raise AiritiError("resume 必須是上次回傳的資料夾名稱（不是路徑）。")
    if not (folder / STATE_FILE).is_file():
        raise AiritiError(f"找不到可續跑的檢索「{name}」（在 {root} 底下沒有 {STATE_FILE}）。")
    return folder


async def start_run(query: str, max_total: int, export_key: str) -> tuple[RunState | None, str]:
    """Fetch page 1. Returns (state, "") or (None, hint-text) when the site reports 0 results."""
    from . import hints

    first = parsers.parse_search(await client.fetch_search_html(query, 1, PAGE_SIZE))
    if first.total == 0:
        return None, hints.few_results_hint(query, 0)
    st = RunState(
        query=query, max_total=max_total, export_key=export_key,
        started=datetime.now().astimezone().isoformat(timespec="seconds"),
        total=first.total, pages_needed=-(-min(first.total, max_total) // PAGE_SIZE),
        pages_fetched=1, fetched=len(first.items), items=[asdict(i) for i in first.items],
    )
    return st, ""


# ------------------------------------------------------------------------ engine ----
def _units_total(st: RunState) -> float:
    return st.pages_needed * (2 if st.export_key else 1)


async def _harvest(st: RunState, folder: Path, deadline: float, progress: ProgressFn) -> None:
    while st.next_page <= st.pages_needed:
        if _now() >= deadline:
            return  # paused: harvest_done stays False
        page = st.next_page
        try:
            res = parsers.parse_search(await client.fetch_search_html(st.query, page, PAGE_SIZE))
        except AiritiError as e:  # keep what we have; the log says the run is incomplete
            st.harvest_error = f"第 {page} 頁：{type(e).__name__}: {e}"
            break
        if not res.items:
            st.harvest_error = f"第 {page} 頁沒有項目（華藝回報 {res.total} 筆），提前停止。"
            break
        st.pages_fetched += 1
        st.fetched += len(res.items)
        st.items += [asdict(i) for i in res.items]
        st.next_page += 1
        save_state(st, folder)
        await progress(st.pages_fetched, _units_total(st), f"已抓 {st.pages_fetched}/{st.pages_needed} 頁")
    # harvest over (all pages, or stopped early on an error): de-duplicate and apply the cap
    seen: set[str] = set()
    deduped = [d for d in st.items if not (d["doc_id"] in seen or seen.add(d["doc_id"]))]
    st.deduped = len(deduped)
    st.items = deduped[: st.max_total]
    items = st.search_items()
    st.ids = [it.doc_id for it in items if not is_thesis(it)]
    st.skipped_theses = len(items) - len(st.ids)
    st.harvest_done = True
    if not st.export_key or not st.ids:
        st.export_done = True
    save_state(st, folder)


async def _export(st: RunState, folder: Path, deadline: float, progress: ProgressFn) -> None:
    site_type, ext, _ = cite_export.FORMATS[st.export_key]
    path = folder / f"export.{ext}"
    while st.export_cursor < len(st.ids):
        if _now() >= deadline:
            return  # paused
        cursor = st.export_cursor
        batch = st.ids[cursor : cursor + MAX_EXPORT]
        parts: list[str] = []
        ok: list[str] = []
        bad: list[str] = []
        stop: AiritiError | None = None
        try:
            content, _ = await export_ids(batch, site_type)
            if content:
                parts, ok = [content], list(batch)
            elif len(batch) == 1:  # already tried twice; nothing left to isolate
                bad = list(batch)
            else:
                for one in batch:  # one invalid DocID fails the whole batch: retry singly (once) to isolate it
                    c, _ = await export_ids([one], site_type, attempts=1)
                    if c:
                        parts.append(c)
                        ok.append(one)
                    else:
                        bad.append(one)
        except AiritiError as e:  # network/parse trouble: stop, keep what was exported
            stop = e
        if parts:
            with path.open("a", encoding="utf-8", newline="") as f:
                f.write(_CRLF2.join(parts) + _CRLF2)
        st.done += ok
        st.failed += bad
        if stop is not None:
            handled = set(st.done) | set(st.failed)
            st.unattempted = [i for i in st.ids[cursor:] if i not in handled]
            st.export_problem = f"{type(stop).__name__}: {stop}"
            st.export_done = True
            save_state(st, folder)
            return
        st.export_cursor += len(batch)
        save_state(st, folder)
        await progress(
            st.pages_needed + st.pages_needed * st.export_cursor / len(st.ids), _units_total(st),
            f"已匯出 {st.export_cursor}/{len(st.ids)} 篇",
        )
    st.export_done = True
    save_state(st, folder)


async def advance(st: RunState, folder: Path, budget_s: float, progress: ProgressFn | None = None) -> bool:
    """Work until finished or `budget_s` seconds have passed. Returns True when the run is complete.
    The deadline is only checked between units (a page, an export batch), so a call can overshoot by
    one unit — normally a few seconds; up to ~70 s when a batch has to be taken apart to find a bad DocID."""
    raw_progress = progress or _no_progress
    last = [-1.0]

    async def progress(done: float, total: float | None, message: str) -> None:
        if done > last[0]:  # MCP requires progress to increase with every notification
            last[0] = done
            await raw_progress(done, total, message)

    t0 = _now()
    deadline = t0 + budget_s
    try:
        if not st.harvest_done:
            await _harvest(st, folder, deadline, progress)
        if st.harvest_done and not st.export_done:
            await _export(st, folder, deadline, progress)
    finally:
        st.calls += 1
        st.elapsed_s += _now() - t0
        save_state(st, folder)
    done = st.harvest_done and st.export_done
    if done:
        write_final_files(st, folder)
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
    if st.failed:
        shown = "、".join(st.failed[:20]) + (f"…（另 {len(st.failed) - 20} 篇）" if len(st.failed) > 20 else "")
        lines.append(f"  【匯出失敗】{len(st.failed)} 篇（華藝拒絕這些 DocID）：{shown}")
    if st.unattempted:
        lines.append(f"  【因中斷未處理】{len(st.unattempted)} 篇（沒有發出匯出請求，不代表 DocID 無效）")
    if st.export_problem:
        lines.append(f"  【匯出中斷】{st.export_problem}")
    return lines


def write_final_files(st: RunState, folder: Path) -> None:
    items = st.search_items()
    _write_csv(items, folder)
    when = datetime.fromisoformat(st.started)
    note = f"執行方式：分 {st.calls} 次呼叫完成，累計約 {st.elapsed_s:.0f} 秒" if st.calls > 1 else ""
    (folder / "search_log.txt").write_text(
        search_export.build_log(
            query=st.query, when=when, reported_total=st.total, fetched=st.fetched, deduped=st.deduped,
            unique=len(items), pages_fetched=st.pages_fetched, page_size=PAGE_SIZE, cap=st.max_total,
            items=items, error=st.harvest_error, export_lines=export_lines(st), run_note=note,
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
        dups = search_export.duplicate_groups(st.search_items())
        if dups:
            out.append(f"疑似重複 {len(set(dups.values()))} 組、涉及 {len(dups)} 筆（篇名與作者相同、DocID 不同；未移除，見 results.csv「疑似重複組」欄與檢索紀錄）。")
        shown = st.search_items()[:5]
        out += ["", "前 5 筆："] + [f"{i}. {it.title}（DocID：{it.doc_id}）" for i, it in enumerate(shown, 1)]
    else:
        out.append(f"目前清單（暫存）：{folder / 'results.csv'}")
    return "\n".join(out)
