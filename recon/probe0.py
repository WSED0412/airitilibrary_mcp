import asyncio, sys, re
sys.stdout.reconfigure(encoding="utf-8")
import logging; logging.disable(logging.CRITICAL)
from airiti_mcp import client
from selectolax.lexbor import LexborHTMLParser as P
async def main():
    h = await client.fetch_search_html("zzzxqjkw不存在的詞", 1, 10)
    open("recon/zero.html","w",encoding="utf-8").write(h)
    t = P(h)
    print("len", len(h))
    for kw in ("無符合","查無","沒有","0 個","找不到","noResult","NoResult","no_result","emptyResult","resultEmpty"):
        for m in re.finditer(kw, h):
            print(kw, "->", repr(re.sub(r"\s+"," ",h[max(0,m.start()-120):m.start()+120]))); break
    print("classes w/ result:", sorted({c for n in t.css("[class]") for c in n.attributes["class"].split() if "esult" in c or "mpty" in c})[:30])
asyncio.run(main())
