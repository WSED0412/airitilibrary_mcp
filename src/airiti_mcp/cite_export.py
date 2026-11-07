"""Parsing for Airiti's own citation export (/Article/CiteWindow + /Article/CiteExport).

The site returns reference-manager files (BibTeX / RIS / EndNote) as JSON:
{"ReturnValue": 0, "ReturnData": {"ID": "<name>.bib", "Content": "<file text>"}}.
ReturnValue 0 = success. We pass the text through unchanged -- it is the site's own export."""
from __future__ import annotations

import json
import re

from .errors import AiritiParseError

# our format name -> (site `type` value, file extension, description)
FORMATS = {
    "bibtex": ("BibTeX", "bib", "BibTeX（Zotero／JabRef／LaTeX）"),
    "ris": ("RIS", "ris", "RIS（Zotero／Mendeley／EndNote／Papers）"),
    "endnote": ("Endnote", "enw", "EndNote 匯入格式（.enw）"),
}

# RIS only: for records without a DOI Airiti writes "UR  - Article/Detail?docID=..." (no host), a dead link
# once imported into a reference manager. BibTeX / EndNote exports carry no URL field, so nothing to fix there.
_RELATIVE_UR = re.compile(r"^(UR  - )(/?Article/[^\r\n]*)", re.M)  # no "$": lines end in CRLF


def absolutize_urls(content: str, base: str) -> str:
    """Make relative `UR` lines in RIS content absolute. Every other byte is left as Airiti sent it."""
    return _RELATIVE_UR.sub(lambda m: m.group(1) + base.rstrip("/") + "/" + m.group(2).lstrip("/"), content)


_TOKEN = re.compile(r"ajaxRequestVerificationToken_CiteWindow\s*=\s*'([^']+)'")


def parse_cite_token(html: str) -> str:
    m = _TOKEN.search(html)
    if not m:
        raise AiritiParseError("引用視窗裡找不到防偽 token（ajaxRequestVerificationToken_CiteWindow）；華藝可能改版。")
    return m.group(1)


def cite_export_error(body: str) -> str:
    """The site's own failure message (ReturnMessage), if any, for error reporting."""
    try:
        return str(json.loads(body).get("ReturnMessage") or "")
    except (ValueError, AttributeError):
        return ""


def parse_cite_export(body: str) -> str | None:
    """Return the export text, or None if the site reported a failure (caller may retry once).
    Raises AiritiParseError if the response is not the JSON shape we know."""
    try:
        obj = json.loads(body)
        ok = obj["ReturnValue"] == 0
        data = obj.get("ReturnData")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise AiritiParseError(f"CiteExport 回應不是預期的 JSON 結構：{body[:120]!r}") from e
    content = data.get("Content") if isinstance(data, dict) else None
    if not ok or not isinstance(content, str) or not content.strip():  # failure, empty, blank or not text
        return None
    # trim the blank lines around the file but keep trailing spaces: RIS ends with the line "ER  - "
    return content.lstrip().rstrip("\r\n")
