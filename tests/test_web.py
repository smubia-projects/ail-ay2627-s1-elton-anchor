"""Focused tests for the demo API (web/): session scoping, limits, uploads, TTL cleanup.

No database or model key needed: db's four query functions are replaced by an
in-memory fake that understands the statements the app sends, and embeddings
and answering are stubbed.

    python -m pytest tests -q
"""
import datetime as dt
import json
import pathlib
import re
import sys
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))

import app as A  # noqa: E402
import db  # noqa: E402
import ingest  # noqa: E402
import pipeline  # noqa: E402
import retrieval  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeDB:
    """files and chunks in memory; every statement must carry the library filter it is given."""

    def __init__(self):
        self.files, self.chunks, self.next_id, self.log = [], [], 1, []

    def _public(self, f, cols):
        return {c: f[c] for c in cols}

    def one(self, sql, args=()):
        self.log.append((sql, args))
        s = " ".join(sql.split())
        if s.startswith("SELECT count(*) AS n FROM files WHERE library_id"):
            return {"n": sum(f["library_id"] == args[0] for f in self.files)}
        if s.startswith("SELECT count(*) AS n FROM chunks WHERE file_id"):
            return {"n": sum(c["file_id"] == args[0] for c in self.chunks)}
        if s.startswith("SELECT 1 AS x FROM files WHERE library_id = %s AND name = %s"):
            return {"x": 1} if any(f["library_id"] == args[0] and f["name"] == args[1] for f in self.files) else None
        if s.startswith("SELECT 1 AS x FROM chunks WHERE library_id"):
            return {"x": 1} if any(c["library_id"] == args[0] for c in self.chunks) else None
        if s.startswith("SELECT id, name, format, pages, body FROM files WHERE id = %s AND library_id = ANY(%s)"):
            f = next((f for f in self.files if f["id"] == args[0] and f["library_id"] in args[1]), None)
            return self._public(f, ("id", "name", "format", "pages", "body")) if f else None
        if s.startswith("INSERT INTO files"):
            lib, name, fmt, pages, body = args
            f = {"id": self.next_id, "library_id": lib, "name": name, "format": fmt, "pages": pages, "body": body,
                 "created": dt.datetime.now(dt.timezone.utc)}
            self.next_id += 1
            self.files.append(f)
            return self._public(f, ("id", "name", "format", "pages", "created"))
        if s.startswith("DELETE FROM files WHERE id = %s AND library_id = %s RETURNING id"):
            return {"id": args[0]} if self._delete(lambda f: f["id"] == args[0] and f["library_id"] == args[1]) else None
        raise AssertionError(f"unexpected one(): {s}")

    def all_(self, sql, args=()):
        self.log.append((sql, args))
        s = " ".join(sql.split())
        if s.startswith("SELECT id, name, format, pages, created FROM files WHERE library_id = %s"):
            return [self._public(f, ("id", "name", "format", "pages", "created"))
                    for f in self.files if f["library_id"] == args[0]]
        if "FROM chunks" in s:  # retrieval: keyword or vector, filtered on the Library
            assert "library_id = %(lib)s" in s
            return [{"chunk_id": c["chunk_id"], "file": c["file"], "loc": c["loc"], "heading": c["heading"],
                     "text": c["text"], "score": 1.0} for c in self.chunks if c["library_id"] == args["lib"]]
        raise AssertionError(f"unexpected all_(): {s}")

    def run(self, sql, args=()):
        self.log.append((sql, args))
        s = " ".join(sql.split())
        if s.startswith("DELETE FROM files WHERE id = %s AND library_id = %s"):
            self._delete(lambda f: f["id"] == args[0] and f["library_id"] == args[1])
        elif s.startswith("DELETE FROM files WHERE library_id <> %s AND created <"):
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=args[1])
            self._delete(lambda f: f["library_id"] != args[0] and f["created"] < cutoff)
        elif s.startswith("DELETE FROM files WHERE library_id = %s"):
            self._delete(lambda f: f["library_id"] == args[0])
        else:
            raise AssertionError(f"unexpected run(): {s}")

    def many(self, sql, rows):
        assert sql.startswith("INSERT INTO chunks")
        for lib, cid, fid, file, loc, heading, text, vec in rows:
            assert any(f["id"] == fid and f["library_id"] == lib for f in self.files), "chunk under another Library"
            self.chunks.append({"library_id": lib, "chunk_id": cid, "file_id": fid, "file": file, "loc": loc,
                                "heading": heading, "text": text, "embedding": vec})

    def _delete(self, match):
        gone = {f["id"] for f in self.files if match(f)}
        self.files = [f for f in self.files if f["id"] not in gone]
        self.chunks = [c for c in self.chunks if c["file_id"] not in gone]  # ON DELETE CASCADE
        return bool(gone)


@pytest.fixture
def fake(monkeypatch):
    f = FakeDB()
    for name in ("one", "all_", "run", "many"):
        monkeypatch.setattr(db, name, getattr(f, name))
    monkeypatch.setattr(db, "configured", lambda: True)
    monkeypatch.setattr(db, "_last_cleanup", 0.0)
    monkeypatch.setattr(retrieval, "embed", lambda texts: (["[0]" for _ in texts], 0))
    monkeypatch.setattr(A, "load_key", lambda: "test-key")
    monkeypatch.setattr(A, "check_rate_limit", lambda request, bucket: None)
    return f


@pytest.fixture
def client(fake):
    return TestClient(A.app)


def sess():
    return {"X-Anchor-Session": str(uuid.uuid4())}


def put(client, headers, name="notes.md", text="# Late work\n\nLabs lose 10% per day late.\n"):
    return client.post("/api/upload", headers=headers, files={"file": (name, text.encode(), "text/markdown")})


def test_health_and_root_are_api_only():
    c = TestClient(A.app)
    assert c.get("/api/health").json() == {"status": "ok"}
    assert "service" in c.get("/").json()
    assert c.get("/app").status_code == 404  # no pages served from Cloud Run


def test_upload_lists_only_in_its_own_session(client, fake):
    a, b = sess(), sess()
    r = put(client, a)
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert [f["name"] for f in client.get("/api/files?library=mine", headers=a).json()["files"]] == ["notes.md"]
    assert client.get("/api/files?library=mine", headers=b).json()["files"] == []
    assert client.get(f"/api/files/{fid}", headers=a).json()["name"] == "notes.md"
    assert client.get(f"/api/files/{fid}", headers=b).status_code == 404
    assert client.get(f"/api/files/{fid}").status_code == 404  # no session: only the sample Library
    assert client.delete(f"/api/files/{fid}", headers=b).status_code == 404
    assert len(fake.files) == 1 and fake.chunks
    assert client.delete(f"/api/files/{fid}", headers=a).json() == {"ok": True}
    assert fake.files == [] and fake.chunks == []


def test_search_never_reads_another_session(client, fake, monkeypatch):
    a, b = sess(), sess()
    put(client, a, text="# Secret\n\nAlpha's private notes.\n")
    put(client, b, name="b.md", text="# Mine\n\nBravo's notes.\n")
    hits = retrieval.search("notes", 5, A._mine(b["X-Anchor-Session"]))
    assert hits and all(h["file"] == "b.md" for h in hits)
    for sql, args in fake.log:
        if "FROM chunks" in sql and isinstance(args, dict):
            assert args["lib"] == A._mine(b["X-Anchor-Session"])


def test_demo_library_is_read_only_and_public(client, fake):
    f = fake.one("INSERT INTO files (library_id, name, format, pages, body) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                 (db.DEMO, "CC101 Course Handbook.md", "Markdown", 1, "# Handbook"))
    assert client.get(f"/api/files/{f['id']}").json()["name"] == "CC101 Course Handbook.md"
    assert client.get("/api/files?library=demo").json()["files"][0]["name"] == "CC101 Course Handbook.md"
    assert client.delete(f"/api/files/{f['id']}", headers=sess()).status_code == 404


def test_session_id_must_be_a_uuid(client):
    for bad in (None, "", "demo", "s:abc", "../etc", str(uuid.uuid4()) + "x"):
        h = {"X-Anchor-Session": bad} if bad is not None else {}
        assert client.get("/api/files?library=mine", headers=h).status_code == 400
        assert put(client, h).status_code == 400


def test_upload_limits(client, fake):
    h = sess()
    for i in range(ingest.MAX_FILES):
        assert put(client, h, name=f"n{i}.txt", text=f"line {i}\n").status_code == 200
    assert put(client, h, name="one-more.txt").status_code == 403
    h2 = sess()
    assert put(client, h2, name="slides.pptx").status_code == 400
    assert put(client, h2, name="big.txt", text="x" * (ingest.MAX_BYTES + 1)).status_code == 413
    assert put(client, h2, name="empty.txt", text="   ").status_code == 400
    assert put(client, h2, name="a.md").status_code == 200
    assert put(client, h2, name="a.md").status_code == 409


def test_upload_takes_one_rate_limit_hit_after_validation(client, monkeypatch):
    hits = []
    monkeypatch.setattr(A, "check_rate_limit", lambda request, bucket: hits.append(bucket))
    h = sess()
    put(client, h, name="bad.docx")
    assert hits == []  # refused before the limiter: costs nothing
    put(client, h)
    assert hits == ["upload"]


def test_upload_429_reaches_the_browser(client, monkeypatch, fake):
    def over(request, bucket):
        raise HTTPException(429, {"message": "Rate limit exceeded.", "queries_used": 3})
    monkeypatch.setattr(A, "check_rate_limit", over)
    r = put(client, sess())
    assert r.status_code == 429 and r.json()["detail"]["queries_used"] == 3
    assert fake.files == []  # nothing indexed after a 429


def test_ttl_cleanup_runs_on_requests(client, fake):
    h = sess()
    fid = put(client, h).json()["id"]
    fake.files[0]["created"] -= dt.timedelta(hours=db.UPLOAD_TTL_HOURS + 1)
    demo = fake.one("INSERT INTO files (library_id, name, format, pages, body) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (db.DEMO, "old demo.md", "Markdown", 1, "# x"))
    fake.files[-1]["created"] -= dt.timedelta(days=30)
    db._last_cleanup = 0.0
    assert client.get("/api/files?library=mine", headers=h).json()["files"] == []
    assert not any(f["id"] == fid for f in fake.files) and not fake.chunks
    assert any(f["id"] == demo["id"] for f in fake.files)  # the sample Library never expires


def test_ask_checks_then_limits_then_streams(client, fake, monkeypatch):
    hits, seen = [], {}
    monkeypatch.setattr(A, "check_rate_limit", lambda request, bucket: hits.append(bucket))

    def stream(lib, q, turns):
        seen.update(lib=lib, q=q, turns=turns)
        yield {"type": "stage", "stage": "reading"}
        yield {"type": "done", "answer": {"final_answer": "ok"}}
    monkeypatch.setattr(A.pipeline, "stream", stream)
    h = sess()
    assert client.post("/api/ask/stream", json={"question": " ", "library": "mine"}, headers=h).status_code == 400
    assert client.post("/api/ask/stream", json={"question": "Late penalty?", "library": "mine"}, headers=h).status_code == 400
    assert hits == []  # empty Library: refused before the limiter
    put(client, h)
    hits.clear()
    r = client.post("/api/ask/stream", headers=h, json={"question": "Late penalty?", "library": "mine",
                                                       "history": [{"question": f"q{i}", "final_answer": "a"} for i in range(5)]})
    assert r.status_code == 200 and hits == ["ask"]
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert events[-1]["type"] == "done"
    assert seen["lib"] == A._mine(h["X-Anchor-Session"]) and len(seen["turns"]) == A.HISTORY_TURNS


def test_ask_without_key_is_501_not_503(client, fake, monkeypatch):
    h = sess()
    put(client, h)
    monkeypatch.setattr(A, "load_key", lambda: "")
    r = client.post("/api/ask/stream", headers=h, json={"question": "Late penalty?", "library": "mine"})
    assert r.status_code == 501


def test_without_database_is_501(monkeypatch):
    monkeypatch.setattr(db, "configured", lambda: False)
    assert TestClient(A.app).get("/api/files?library=demo").status_code == 501


def test_ingest_reads_formats_with_locations():
    body, pages = ingest.read("hints.txt", "Text", b"Task 1: Start\nUse port 8080.\n\nTask 2: Copy\nscp -r\n")
    chunks = ingest.make_chunks("hints.txt", "Text", body, 7)
    assert pages == 1 and chunks[0]["chunk_id"] == "f7#001" and re.fullmatch(r"lines \d+-\d+", chunks[0]["loc"])
    md = (ROOT / "demo-library" / "CC101 Course Handbook.md").read_text(encoding="utf-8")
    body, _ = ingest.read("h.md", "Markdown", md.encode())
    chunks = ingest.make_chunks("h.md", "Markdown", body, 1)
    assert chunks and all(c["heading"] is not None for c in chunks)


def test_ingest_pdf_pages(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    out = tmp_path / "blank.pdf"
    pdf.save(str(out))
    with pytest.raises(ingest.Refused, match="scan"):
        ingest.read("blank.pdf", "PDF", out.read_bytes())
    with pytest.raises(ingest.Refused, match="could not be opened"):
        ingest.read("bad.pdf", "PDF", b"not a pdf")


def test_pipeline_uses_no_eval_files(monkeypatch):
    """The demo pipeline must not reach the eval Library (pictures, DuckDB): those files aren't deployed."""
    import answering
    calls = []

    class C:
        pass
    monkeypatch.setattr(pipeline, "_c", lambda: C())
    monkeypatch.setattr(pipeline.router, "route", lambda c, q: ("lookup", None))
    monkeypatch.setattr(pipeline.retrieval, "search", lambda q, k, lib: calls.append(lib) or [
        {"chunk_id": "f1#001", "file": "a.md", "loc": None, "heading": "Late", "text": "Labs lose 10% per day late."}])
    monkeypatch.setattr(answering, "stream_json", lambda c, prompt, on_text, **kw: (
        {"not_found": False, "final_answer": "10% per day.",
         "steps": [{"text": "The rule.", "quote": "Labs lose 10% per day late.", "source": "f1#001"}]}, {}))
    monkeypatch.setattr(pipeline.checker, "check_support", lambda c, q, r: ({"steps": [{"n": 1, "supported": True}],
                                                                            "final_supported": True}, None))
    monkeypatch.setattr("number_check.check_numbers", lambda c, q, r: ([], None))
    events = list(pipeline.stream("s:x", "Late penalty?"))
    assert calls == ["s:x"] and events[-1]["type"] == "done", events[-1]
    done = events[-1]["answer"]
    assert done["unsure"] is False and done["pictures"] == [] and done["steps"][0]["source"]["file"] == "a.md"
