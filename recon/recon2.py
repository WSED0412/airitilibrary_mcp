import json, re, time, urllib.parse, html, urllib.request
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) airiti-mcp-recon/0.1"
def call(url, post):
    r=urllib.request.Request(url, data=b"" if post else None, headers={"User-Agent":UA,"Accept-Language":"zh-TW"}, method="POST" if post else "GET")
    with urllib.request.urlopen(r,timeout=15) as x: return x.status, x.read().decode("utf-8","replace")
def summ(tag,s,b):
    ids=re.findall(r"_\u6587\u7ae0\u66f8\u76ee_\u9ede\u64ca\u7bc7\u540d\('([^']+)'",b)
    n=re.search(r'resultReport">([^<]+)<',b)
    print(tag,s,len(b),ascii(n.group(1) if n else None),len(ids),ascii(ids[:2]))
    return ids
b1=open("recon/out_direct.html",encoding="utf-8").read()
ids1=re.findall(r"_\u6587\u7ae0\u66f8\u76ee_\u9ede\u64ca\u7bc7\u540d\('([^']+)'",b1)
href=re.findall(r'<li class="page"><a href="(/Article/Query\?queryString=[^"]+)">2</a>',b1)[0]
# A: GET page-2 link as the site renders it
s,b=call("https://www.airitilibrary.com"+html.unescape(href),False); idsA=summ("A GET page2 link",s,b); time.sleep(1.6)
# B: user's compact JSON + DSF.Page=2, POST
q={"\u67e5\u8a62\u6642\u9593":"2026/11/03 05:27:02","\u67e5\u8a62\u6b77\u53f2\u985e\u578b\u4ee3\u78bc":"ADLang","\u662f\u5426\u95dc\u9375\u5b57\u7d71\u8a08":True,
 "DSF":{"SearchFileds":[{"FieldName":49,"SearchKeyWord":"\u9df9\u67b6 \u6578\u5b78\u89e3\u984c","FieldQuery":True}],"IsFuzzySearch":False,"Page":2,"PageSize":10},
 "BSF":{"SearchFiledList":[{"FieldName":0,"SearchKeyWord":"\u9df9\u67b6","FieldQuery":True,"FieldLogic":0},{"FieldName":0,"SearchKeyWord":"\u6578\u5b78\u89e3\u984c","FieldQuery":True,"FieldLogic":0}]}}
u="https://www.airitilibrary.com/Article/Query?queryString="+urllib.parse.quote(json.dumps(q,ensure_ascii=False,separators=(",",":")))
s,b=call(u,True); idsB=summ("B POST compact Page=2",s,b); time.sleep(1.6)
print("A==B ids:",idsA==idsB,"disjoint from page1:",not set(idsA)&set(ids1))
# C: BSF omitted entirely (simplest possible query)
q2={"DSF":q["DSF"]}; 
u2="https://www.airitilibrary.com/Article/Query?queryString="+urllib.parse.quote(json.dumps(q2,ensure_ascii=False,separators=(",",":")))
try:
    s,b=call(u2,True); summ("C DSF only",s,b)
except Exception as e: print("C failed",e)
# FieldName enum hints in page-1 JS
for m in re.finditer(r"FieldName\s*[=:]\s*[\w\.\u4e00-\u9fff]+",b1): pass
enums=set(re.findall(r"(?:\u6b04\u4f4d|Field)[\w\u4e00-\u9fff]*\.[\w\u4e00-\u9fff]+",b1)); print(ascii(sorted(enums)[:40]))
