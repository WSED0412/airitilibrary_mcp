import asyncio, sys
sys.stdout.reconfigure(encoding="utf-8")
from airiti_mcp import server as s
async def main():
    print(await s.airiti_search("鷹架 數學解題", 1, 3)); print("="*40)
    print(await s.airiti_search("鷹架 數學解題", 2, 20)); print("="*40)
    print(await s.airiti_abstract("P20240112001-N202410010002-00002")); print("="*40)
    print(await s.airiti_cite_apa("P20240112001-N202410010002-00002")); print("="*40)
    for bad in ("U0030-0405201214560638", "NOPE-does-not-exist"):
        try: print(bad, "->", (await s.airiti_abstract(bad))[:600])
        except Exception as e: print(bad, "-> ERR", type(e).__name__, e)
        print("="*40)
    print(await s.airiti_search("zzzxqjkw不存在的詞"))
asyncio.run(main())
