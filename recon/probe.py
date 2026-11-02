import asyncio, sys, re
sys.stdout.reconfigure(encoding="utf-8")
import logging; logging.disable(logging.CRITICAL)
from airiti_mcp import client
from selectolax.lexbor import LexborHTMLParser as P
async def main():
    h = await client.fetch_search_html("鷹架 數學解題", 1, 3)
    t = P(h)
    print("checkbox keys:", [n.attributes.get("key") for n in t.css("input.Result_選擇")])
    print("onclick ids:", re.findall(r"點擊篇名\('([^']+)'", h))
    d = await client.fetch_detail_html("U0021-1610201315285936")
    open("recon/thesis.html","w",encoding="utf-8").write(d)
    t = P(d)
    print("thesis len", len(d), "title:", t.css_first("title").text(strip=True)[:80] if t.css_first("title") else None)
    print("metas:", sorted({(n.attributes.get("name") or n.attributes.get("property")) for n in t.css("meta") if (n.attributes.get("name") or n.attributes.get("property") or "").startswith(("citation","DC","og"))}))
    for n in t.css("meta[name^=citation]"): print(" ", n.attributes["name"], "=", (n.attributes.get("content") or "")[:60])
    print("sets:", [n.attributes.get("class") for n in t.css("div[class*=Abstract]")][:8])
    await asyncio.sleep(1.6)
    d2 = await client.fetch_detail_html("NOPE-does-not-exist")
    t2 = P(d2); print("NOPE len", len(d2), "title:", t2.css_first("title").text(strip=True)[:80] if t2.css_first("title") else None)
    print("NOPE body text:", re.sub(r"\s+"," ", t2.body.text(strip=True))[:200] if t2.body else None)
asyncio.run(main())
