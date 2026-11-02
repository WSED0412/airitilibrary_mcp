from selectolax.lexbor import LexborHTMLParser as HTMLParser
t=HTMLParser(open("recon/detail.html",encoding="utf-8").read())
def dump(n,d=0):
    if n.tag=="-text":
        s=n.text(strip=True)
        if s: print("  "*d+ascii(s[:50]))
        return
    print("  "*d+"<"+n.tag+" "+ascii(n.attributes.get("class") or "")+">")
    for c in n.iter(include_text=True): dump(c,d+1)
for sel in ["div.academicAbstractSet","div.academicAlignAbstractSet"]:
    n=t.css_first(sel); print("=====",sel); dump(n)
print("authors",[ascii(m.attributes["content"]) for m in t.css("meta[name=citation_author]")])
print("kw full",ascii(t.css_first("meta[name=citation_keywords]").attributes["content"]))
print("ids:",[ascii(n.attributes.get("id")) for n in t.css("[id^=Detail_]")][:14])
