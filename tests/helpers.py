"""Shared helpers for the offline tests. The fixtures that use them live in conftest.py."""
import csv
import io

from airiti_mcp import server
from airiti_mcp.parsers import SearchItem, SearchPage

CRLF = chr(13) + chr(10)


def _item(n: int, kind: str = "期刊") -> SearchItem:
    doc_id = f"U{n}" if kind == "學位論文" else f"DOC{n}"
    return SearchItem(doc_id, f"篇名{n}", [f"作者{n}", "乙"], "刊名/2024", kind, f"https://x/{n}", n % 2 == 0)


def _rows(d):
    return list(csv.reader(io.StringIO((d / "results.csv").read_bytes().decode("utf-8-sig"), newline="")))


def _only_run_dir(root):
    dirs = [d for d in root.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    return dirs[0]


def _run_dirs(root):
    return [d for d in root.iterdir() if d.is_dir()]


def _it(doc_id, title, authors, source="期刊A 12卷1期 (2025/04) Pp. 39-70"):
    return SearchItem(doc_id, title, authors, source, "期刊", f"https://x/{doc_id}", False)


def _tied_boundary_site(monkeypatch, tails, heads):
    """45 records, 20 per page. Records 19 and 20 are tied in rank and straddle the page 1|2 boundary; the site
    orders them anew on every request: request n of page 1 ends with tails[n], request n of page 2 starts with heads[n]."""
    calls = {"1": 0, "2": 0}

    def parse(html):
        n = calls.get(html, 0)
        calls[html] = n + 1
        if html == "1":
            tail = tails[min(n, len(tails) - 1)]
            return SearchPage(total=45, items=[_item(i) for i in range(0, 19)] + [_item(tail)])
        if html == "2":
            head = heads[min(n, len(heads) - 1)]
            return SearchPage(total=45, items=[_item(head)] + [_item(i) for i in range(21, 40)])
        return SearchPage(total=45, items=[_item(i) for i in range(40, 45)])

    monkeypatch.setattr(server.parsers, "parse_search", parse)
