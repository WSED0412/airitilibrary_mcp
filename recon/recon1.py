import json, urllib.request, urllib.parse, http.cookiejar, re, time, sys
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) airiti-mcp-recon/0.1"
H = {"User-Agent": UA, "Accept-Language": "zh-TW"}
def req(url, data=None, extra=None):
    h = dict(H); h.update(extra or {})
    r = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        resp = op.open(r, timeout=15)
        return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")
q = {"查詢時間":"2026/11/03 05:27:02","查詢歷史類型代碼":"ADLang","是否關鍵字統計":True,
 "DSF":{"SearchFileds":[{"FieldName":49,"SearchKeyWord":"鷹架 數學解題","FieldQuery":True}],"IsFuzzySearch":False},
 "BSF":{"SearchFiledList":[{"FieldName":0,"SearchKeyWord":"鷹架","FieldQuery":True,"FieldLogic":0},
 {"FieldName":0,"SearchKeyWord":"數學解題","FieldQuery":True,"FieldLogic":0}]}}
url = "https://www.airitilibrary.com/Article/Query?queryString=" + urllib.parse.quote(json.dumps(q, ensure_ascii=False, separators=(",",":")))
mode = sys.argv[1] if len(sys.argv) > 1 else "direct"
if mode == "cookie":
    s, h, b = req("https://www.airitilibrary.com/"); print("home", s, [c.name for c in cj]); time.sleep(1.5)
s, h, b = req(url, data=b"")
print("status", s, "len", len(b)); print({k:v for k,v in h.items() if k.lower() in ("content-type","set-cookie","location")})
open(f"recon/out_{mode}.html","w",encoding="utf-8").write(b)
print("results:", re.findall(r"[\d,]+\s*results", b)[:3])
print("docid-ish:", re.findall(r"DocID=([^\"'&<>\s]+)", b)[:5])
print("token:", re.findall(r"RequestVerificationToken[^>]{0,200}", b)[:2])
print("captcha:", [w for w in ("captcha","recaptcha","cloudflare","challenge") if w in b.lower()])
