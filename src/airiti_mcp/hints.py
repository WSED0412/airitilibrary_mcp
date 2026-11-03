"""Query hints. Airiti matches literal characters, so a query written with one variant
form misses documents that use the other (e.g. 沉浸 vs 沈浸). Suggestions only -- we never
silently rewrite the user's query."""
from __future__ import annotations

# Common Traditional-Chinese variant pairs (both directions are generated).
_PAIRS = ["沉沈", "台臺", "裡裏", "線綫", "群羣", "峰峯", "啟啓", "溫温", "週周", "著着", "衛衞", "污汙"]
_VARIANTS: dict[str, str] = {}
for _p in _PAIRS:
    if len(_p) == 2 and _p[0] != _p[1]:
        _VARIANTS[_p[0]] = _p[1]
        _VARIANTS[_p[1]] = _p[0]

FEW_RESULTS = 5  # below this many total hits, show the hint
MAX_SUGGESTIONS = 3


def variant_queries(query: str) -> list[str]:
    """One suggestion per distinct variant character found in `query` (that character swapped
    everywhere), at most MAX_SUGGESTIONS, never equal to the original."""
    out: list[str] = []
    for ch in dict.fromkeys(query):  # unique, order-preserving
        alt = _VARIANTS.get(ch)
        if alt:
            q = query.replace(ch, alt)
            if q != query and q not in out:
                out.append(q)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def few_results_hint(query: str, total: int) -> str:
    lines = [f"提示：只找到 {total} 筆。華藝是逐字比對，用字不同（異體字、簡繁、同義詞、中英文）的文獻不會出現在這次結果裡。"]
    alts = variant_queries(query)
    if alts:
        lines.append("可以試試異體字寫法：" + "、".join(f"「{a}」" for a in alts))
    lines.append("也可以：減少關鍵字、改用同義詞，或加上英文關鍵字。")
    return "\n".join(lines)
