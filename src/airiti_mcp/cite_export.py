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

_TOKEN = re.compile(r"ajaxRequestVerificationToken_CiteWindow\s*=\s*'([^']+)'")


def parse_cite_token(html: str) -> str:
    m = _TOKEN.search(html)
    if not m:
        raise AiritiParseError("引用視窗裡找不到防偽 token（ajaxRequestVerificationToken_CiteWindow）；華藝可能改版。")
    return m.group(1)


def parse_cite_export(body: str) -> str | None:
    """Return the export text, or None if the site reported a failure (caller may retry once).
    Raises AiritiParseError if the response is not the JSON shape we know."""
    try:
        obj = json.loads(body)
        ok = obj["ReturnValue"] == 0
        data = obj.get("ReturnData")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise AiritiParseError(f"CiteExport 回應不是預期的 JSON 結構：{body[:120]!r}") from e
    if not ok or not isinstance(data, dict) or not data.get("Content"):
        return None
    return data["Content"].strip()
