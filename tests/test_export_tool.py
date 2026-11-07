"""airiti_export_citation and cite_export, offline: formats, saving, auto-save, URL completion."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from airiti_mcp import harvest, server
from helpers import CRLF, _only_run_dir, _run_dirs


async def test_export_citation_tool_validation_and_output(fake_site):
    state, root = fake_site
    for bad in ([], [""], [f"D{i}" for i in range(21)]):
        with pytest.raises(ToolError):
            await server.airiti_export_citation(bad, "ris")
    with pytest.raises(ToolError, match="不支援"):
        await server.airiti_export_citation(["DOC1"], "pdf")
    out = await server.airiti_export_citation(["DOC1", "DOC2", "DOC1"], "ris")  # duplicate dropped
    assert "共 2 篇" in out and out.count("TY  - JOUR") == 2 and state["export_calls"] == [["DOC1", "DOC2"]]
    one = await server.airiti_export_citation("DOC9", "bibtex")  # a bare string is accepted
    assert "共 1 篇" in one and state["export_calls"][-1] == ["DOC9"] and "@article{DOC9" in one  # and it IS BibTeX


async def test_export_citation_tool_reports_site_message_on_failure(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC1"
    with pytest.raises(ToolError, match="處理失敗"):
        await server.airiti_export_citation(["DOC1", "DOC2"], "ris")
    assert len(state["export_calls"]) == 2  # one retry, then give up


def test_cite_export_keeps_the_trailing_space_of_the_ris_end_tag():
    from airiti_mcp.cite_export import parse_cite_export

    body = json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": "TY  - JOUR" + CRLF + "ER  - " + CRLF + CRLF}})
    assert parse_cite_export(body) == "TY  - JOUR" + CRLF + "ER  - "  # was "ER  -" before


async def test_export_citation_save_writes_a_file_and_keeps_content_out_of_the_reply(fake_site):
    state, root = fake_site
    out = await server.airiti_export_citation(["DOC1", "DOC2"], "ris", save=True)
    assert "TY  - JOUR" not in out and "共 2 篇" in out and "已存檔" in out
    d = _only_run_dir(root)
    assert str(d / "export.ris") in out and d.parent == root and "export_2" in d.name
    raw = (d / "export.ris").read_bytes().decode("utf-8")
    assert raw.count("TY  - JOUR") == 2 and raw.count("ER  - " + CRLF) == 2 and raw.endswith(CRLF)
    assert not (d / "state.json").exists()  # a one-shot export is not a resumable run
    # a different export (other id count -> other folder name) gets its own folder and leaves the first alone
    # (the real same-name collision is covered by test_export_folder_name_collision_gets_a_suffix)
    await server.airiti_export_citation(["DOC3"], "bibtex", save=True)
    assert len(_run_dirs(root)) == 2
    assert (d / "export.ris").read_bytes().decode("utf-8") == raw
    # default behaviour is unchanged: the text comes back in the reply and nothing new is written
    inline = await server.airiti_export_citation(["DOC1"], "ris")
    assert "TY  - JOUR" in inline and len(_run_dirs(root)) == 2


async def test_export_citation_save_failure_writes_nothing(fake_site):
    state, root = fake_site
    state["bad_id"] = "DOC1"
    with pytest.raises(ToolError, match="處理失敗"):
        await server.airiti_export_citation(["DOC1"], "ris", save=True)
    assert _run_dirs(root) == []


def test_absolutize_urls_touches_only_relative_ur_lines():
    from airiti_mcp.cite_export import absolutize_urls

    base = "https://www.airitilibrary.com"
    ris = (
        "TY  - JOUR" + CRLF + "UR  - Article/Detail?docID=P1-2" + CRLF + "ER  - " + CRLF + CRLF
        + "TY  - JOUR" + CRLF + "UR  - https://doi.org/10.1/x" + CRLF + "AB  - see Article/Detail?docID=Z in the text" + CRLF + "ER  - " + CRLF
    )
    out = absolutize_urls(ris, base)
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=P1-2" + CRLF in out
    assert "UR  - https://doi.org/10.1/x" in out and "AB  - see Article/Detail?docID=Z in the text" in out
    assert out.count("ER  - " + CRLF) == 2 and out.count(CRLF) == ris.count(CRLF)  # nothing else moved
    assert absolutize_urls(out, base) == out  # idempotent
    for other in ("@article{a,\r\nDOI ={10.1/x},\r\n}", "%0 Journal Article" + CRLF + "%T t" + CRLF):
        assert absolutize_urls(other, base) == other  # BibTeX / EndNote are never changed


async def test_export_path_completes_relative_urls_end_to_end(fake_site, monkeypatch):
    state, root = fake_site

    async def no_doi_site(ids, site_type):
        content = "".join("TY  - JOUR" + CRLF + "T1  - " + i + CRLF + "UR  - Article/Detail?docID=" + i + CRLF + "ER  - " + CRLF + CRLF for i in ids)
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    monkeypatch.setattr(server.client, "fetch_cite_export", no_doi_site)
    out = await server.airiti_export_citation(["DOC1"], "ris")
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=DOC1" in out
    await server.airiti_search_all("x", export_format="ris")
    d = [p for p in root.iterdir() if p.is_dir()][0]
    merged = (d / "export.ris").read_bytes().decode("utf-8")
    assert merged.count("UR  - https://www.airitilibrary.com/Article/") == 45 and "UR  - Article/" not in merged
    assert "已補成完整網址" in (d / "search_log.txt").read_text(encoding="utf-8")


async def test_export_folder_name_collision_gets_a_suffix(fake_site, monkeypatch):
    from datetime import datetime as real_datetime

    class Frozen(real_datetime):  # two saves in "the same second" without depending on the wall clock
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 11, 6, 15, 0, 0)

    monkeypatch.setattr(server, "datetime", Frozen)
    state, root = fake_site
    first = await server.airiti_export_citation(["DOC1"], "ris", save=True)
    second = await server.airiti_export_citation(["DOC2"], "ris", save=True)  # same id count -> same folder name
    names = sorted(p.name for p in _run_dirs(root))
    assert len(names) == 2 and names[0].endswith("_export_1") and names[1].endswith("_export_1_2")
    assert (root / names[0] / "export.ris").read_bytes().decode("utf-8").count("T1  - DOC1") == 1  # not overwritten
    assert (root / names[1] / "export.ris").read_bytes().decode("utf-8").count("T1  - DOC2") == 1
    assert names[0] in first and names[1] in second


async def test_export_citation_save_cleans_up_when_writing_fails(fake_site, monkeypatch):
    from pathlib import Path

    state, root = fake_site
    real_open = Path.open

    def full_disk(self, mode="r", *a, **k):
        if "w" in mode and self.name == "export.ris":
            raise OSError(28, "No space left on device")
        return real_open(self, mode, *a, **k)

    monkeypatch.setattr(Path, "open", full_disk)
    with pytest.raises(ToolError, match="No space left on device.*AIRITI_EXPORT_DIR"):  # the model gets the reason + a way out
        await server.airiti_export_citation(["DOC1"], "ris", save=True)
    assert _run_dirs(root) == []  # no empty folder left behind


async def test_save_true_writes_absolute_urls_to_disk(fake_site, monkeypatch):
    state, root = fake_site

    async def no_doi(ids, site_type):
        content = "".join("TY  - JOUR" + CRLF + "UR  - Article/Detail?docID=" + i + CRLF + "ER  - " + CRLF + CRLF for i in ids)
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    monkeypatch.setattr(server.client, "fetch_cite_export", no_doi)
    await server.airiti_export_citation(["DOC1"], "ris", save=True)
    raw = (_only_run_dir(root) / "export.ris").read_bytes().decode("utf-8")
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=DOC1" + CRLF in raw and "UR  - Article/" not in raw


async def test_only_ris_content_gets_urls_rewritten(fake_site, monkeypatch):
    state, root = fake_site
    # a BibTeX / EndNote body whose text happens to contain a line shaped like an RIS UR line
    odd = "@article{a," + CRLF + "abstract ={first" + CRLF + "UR  - Article/Detail?docID=Z" + CRLF + "},}" + CRLF

    async def serve(ids, site_type):
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": odd}})

    monkeypatch.setattr(server.client, "fetch_cite_export", serve)
    bib, _ = await harvest.export_ids(["DOC1"], "BibTeX")
    enw, _ = await harvest.export_ids(["DOC1"], "Endnote")
    ris, _ = await harvest.export_ids(["DOC1"], "RIS")
    assert "UR  - Article/Detail?docID=Z" in bib and "UR  - Article/Detail?docID=Z" in enw  # untouched
    assert "UR  - https://www.airitilibrary.com/Article/Detail?docID=Z" in ris


async def test_a_big_export_is_saved_to_a_file_automatically(fake_site, monkeypatch):
    state, root = fake_site
    monkeypatch.setattr(server, "EXPORT_INLINE_LIMIT", 50)
    big = await server.airiti_export_citation(["DOC1", "DOC2", "DOC3"], "ris")  # three records = 100 characters > 50
    assert "自動存檔" in big and "內容沒有放進對話" in big and "TY  - " not in big
    d = _only_run_dir(root)
    assert (d / "export.ris").read_bytes().decode("utf-8").count("TY  - JOUR") == 3
    small = await server.airiti_export_citation(["DOC4"], "ris")  # one record = 32 characters stays in the reply
    assert "TY  - JOUR" in small and "自動存檔" not in small and len(_run_dirs(root)) == 1


def test_parse_cite_export_is_strict_about_what_counts_as_content():
    from airiti_mcp.cite_export import parse_cite_export

    def body(content):
        return json.dumps({"ReturnValue": 0, "ReturnData": {"ID": "x", "Content": content}})

    assert parse_cite_export(body("   " + CRLF + "  ")) is None  # blank
    assert parse_cite_export(body(123)) is None and parse_cite_export(body(["a"])) is None and parse_cite_export(body(None)) is None
    assert parse_cite_export(body(CRLF + "TY  - JOUR" + CRLF + "ER  - " + CRLF)) == "TY  - JOUR" + CRLF + "ER  - "
