"""Query hints for sparse results.

Airiti matches every whitespace-separated keyword (AND), word by word. Verified live (2026-11-03):
  * Variants the site already treats as equal (no hint needed): 沉/沈, 群/羣, 溫/温, 週/周, 峰/峯, 台/臺.
  * Variants the site does NOT unify (hit counts differ) -> suggested below:
    啟/啓, 線/綫, 楣/霉, 裡/裏, 衛/衞, 污/汙.
  * Different wording in the documents themselves (e.g. 幼稚園 vs 幼兒園) -> suggest dropping a keyword.
Suggestions only; we never silently rewrite the user's query."""
from __future__ import annotations

FEW_RESULTS = 5  # below this many total hits, show the hint
MAX_SUGGESTIONS = 3

_PAIRS = ["啟啓", "線綫", "楣霉", "裡裏", "衛衞", "污汙"]
_VARIANTS: dict[str, str] = {}
for _p in _PAIRS:
    _VARIANTS[_p[0]] = _p[1]
    _VARIANTS[_p[1]] = _p[0]


def variant_queries(query: str) -> list[str]:
    """One suggestion per distinct not-unified variant character in `query` (swapped everywhere)."""
    out: list[str] = []
    for ch in dict.fromkeys(query):
        alt = _VARIANTS.get(ch)
        if alt:
            q = query.replace(ch, alt)
            if q not in out:
                out.append(q)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def drop_one_queries(query: str) -> list[str]:
    """Queries with one keyword removed (multi-keyword queries only)."""
    words = query.split()
    if len(words) < 2:
        return []
    return [" ".join(words[:i] + words[i + 1:]) for i in range(len(words))][:MAX_SUGGESTIONS]


def few_results_hint(query: str, total: int) -> str:
    lines = [
        f"提示：只找到 {total} 筆。華藝要求每個關鍵字都符合（AND），而且逐詞比對；"
        "文獻用詞不同（例如「幼稚園」與「幼兒園」、中英文）就不會出現。"
    ]
    variants = variant_queries(query)
    if variants:
        lines.append("華藝對這些字的異體寫法沒有統一（筆數不同），可試另一種寫法：" + "、".join(f"「{v}」" for v in variants))
    alts = drop_one_queries(query)
    if alts:
        lines.append("可以試試少一個關鍵字：" + "、".join(f"「{a}」" for a in alts))
    lines.append("也可以改用同義詞、較早年代的舊稱，或加上英文關鍵字。")
    return chr(10).join(lines)
