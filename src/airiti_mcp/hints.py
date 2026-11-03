"""Query hints for sparse results.

Verified against the live site: Airiti already treats variant characters (沉/沈) as the same,
and also maps Simplified input. What actually causes misses is that EVERY whitespace-separated
keyword must match (AND), and that documents use different wording (e.g. 幼稚園 vs 幼兒園).
So the useful suggestion is to drop a keyword or try a synonym. We only suggest; we never
silently rewrite the user's query."""
from __future__ import annotations

FEW_RESULTS = 5  # below this many total hits, show the hint
MAX_SUGGESTIONS = 3


def drop_one_queries(query: str) -> list[str]:
    """Queries with one keyword removed (only for multi-keyword queries), at most MAX_SUGGESTIONS."""
    words = query.split()
    if len(words) < 2:
        return []
    return [" ".join(words[:i] + words[i + 1:]) for i in range(len(words))][:MAX_SUGGESTIONS]


def few_results_hint(query: str, total: int) -> str:
    lines = [
        f"提示：只找到 {total} 筆。華藝要求每個關鍵字都符合（AND），而且逐詞比對；"
        "文獻用詞不同（例如「幼稚園」與「幼兒園」、中英文）就不會出現。"
    ]
    alts = drop_one_queries(query)
    if alts:
        lines.append("可以試試少一個關鍵字：" + "、".join(f"「{a}」" for a in alts))
    lines.append("也可以改用同義詞、較早年代的舊稱，或加上英文關鍵字。")
    return "\n".join(lines)
