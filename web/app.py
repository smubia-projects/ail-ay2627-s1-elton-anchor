"""Anchor's API for the AI Lodge demo (Cloud Run). API only: the pages are on Vercel.

    uvicorn app:app --app-dir web --port 8080      # from the repo root

No accounts. Two kinds of Library (db.py):

  demo     the open-licence sample Library (scripts/load_demo_library.py), for everyone
  mine     this browser's own uploads, keyed by the anonymous session id the page
           keeps in localStorage and sends as X-Anchor-Session; deleted
           UPLOAD_TTL_HOURS after upload

The chat history stays in the browser: a follow-up sends its last few turns.

Routes:
  GET    /api/health
  GET    /api/files?library=demo|mine     the Library's files
  GET    /api/files/{id}                  one file's readable copy (citations open it)
  POST   /api/upload                      add a file to "mine"          (limit: upload)
  DELETE /api/files/{id}                  delete one of "mine"
  POST   /api/ask/stream                  answer a question, streamed   (limit: ask)
"""
import json
import os
import pathlib
import re
import sys

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import db  # noqa: E402
import ingest  # noqa: E402
import pipeline  # noqa: E402
from openai_client import load_key  # noqa: E402  (scripts/, put on the path by pipeline)
from rate_limit import check_rate_limit  # noqa: E402

HISTORY_TURNS = 3  # follow-ups see the last few turns
SESSION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

app = FastAPI(title="Anchor API (AI Lodge demo)", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Anchor-Session"],
)


@app.get("/")
def root():
    return {"service": "ail-ay2627-s1-elton-anchor", "docs": "API only; the app is on Vercel"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _db():
    if not db.configured():
        raise HTTPException(501, "The demo's database is not configured (DATABASE_URL).")
    db.cleanup()


def _ai():
    if not load_key():
        raise HTTPException(501, "AI is not configured on this server (API_KEY).")


def _mine(session):
    """This browser's Library id, from its anonymous session id."""
    s = (session or "").strip().lower()
    if not SESSION.fullmatch(s):
        raise HTTPException(400, "Missing or invalid session id. Reload the page and try again.")
    return f"s:{s}"


def _library(which, session):
    if which == "demo":
        return db.DEMO
    if which == "mine":
        return _mine(session)
    raise HTTPException(400, "Choose the sample Library or your own files.")


@app.get("/api/files")
def files(library: str = "demo", x_anchor_session: str | None = Header(None)):
    _db()
    lib = _library(library, x_anchor_session)
    rows = db.all_("SELECT id, name, format, pages, created FROM files WHERE library_id = %s ORDER BY created, id", (lib,))
    out = {"library": library, "files": [r | {"created": r["created"].isoformat()} for r in rows]}
    if library == "mine":
        out |= {"max_files": ingest.MAX_FILES, "ttl_hours": db.UPLOAD_TTL_HOURS}
    return out


@app.get("/api/files/{file_id}")
def file_text(file_id: int, x_anchor_session: str | None = Header(None)):
    """A file's readable copy: the sample Library's for anyone, an upload only for its own browser."""
    _db()
    libs = [db.DEMO]
    if x_anchor_session:
        libs.append(_mine(x_anchor_session))
    f = db.one("SELECT id, name, format, pages, body FROM files WHERE id = %s AND library_id = ANY(%s)", (file_id, libs))
    if not f:
        raise HTTPException(404, "No such file in your Library.")
    return f


@app.post("/api/upload")
def upload(request: Request, file: UploadFile = File(...), x_anchor_session: str | None = Header(None)):
    _db()
    lib = _mine(x_anchor_session)
    name = pathlib.Path(file.filename or "").name.strip()[:120]
    fmt = ingest.fmt_of(name)
    if not name or not fmt:
        raise HTTPException(400, "This demo reads PDFs with selectable text, and notes (.txt, .md).")
    if db.one("SELECT count(*) AS n FROM files WHERE library_id = %s", (lib,))["n"] >= ingest.MAX_FILES:
        raise HTTPException(403, f"This demo keeps up to {ingest.MAX_FILES} files per visitor. Delete one to add another.")
    if db.one("SELECT 1 AS x FROM files WHERE library_id = %s AND name = %s", (lib, name)):
        raise HTTPException(409, f"{name} is already in your Library. Delete it first to add it again.")
    data = file.file.read(ingest.MAX_BYTES + 1)
    if len(data) > ingest.MAX_BYTES:
        raise HTTPException(413, f"Files can be up to {ingest.MAX_BYTES // 2**20} MB in this demo.")
    try:
        body, pages = ingest.read(name, fmt, data)
    except ingest.Refused as e:
        raise HTTPException(400, str(e))
    _ai()
    check_rate_limit(request, "upload")  # embedding the file's passages is a model call
    try:
        row = ingest.add(lib, name, fmt, body, pages)
    except ingest.Refused as e:
        raise HTTPException(400, str(e))
    except Exception:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{name} could not be indexed: the AI service failed. Please try again.")
    return row | {"created": row["created"].isoformat()}


@app.delete("/api/files/{file_id}")
def delete_file(file_id: int, x_anchor_session: str | None = Header(None)):
    """Delete-everywhere: the file and every passage indexed from it."""
    _db()
    lib = _mine(x_anchor_session)
    if not db.one("DELETE FROM files WHERE id = %s AND library_id = %s RETURNING id", (file_id, lib)):
        raise HTTPException(404, "No such file in your Library.")
    return {"ok": True}


class Turn(BaseModel):
    question: str = Field(max_length=1000)
    final_answer: str = Field("", max_length=2000)


class Ask(BaseModel):
    question: str
    library: str = "demo"
    history: list[Turn] = []


@app.post("/api/ask/stream")
def ask_stream(body: Ask, request: Request, x_anchor_session: str | None = Header(None)):
    """An answer, sent while it is made: one JSON event per line (pipeline.stream)."""
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Type a question.")
    if len(q) > 1000:
        raise HTTPException(400, "Please keep questions under 1,000 characters.")
    _db()
    lib = _library(body.library, x_anchor_session)
    if not db.one("SELECT 1 AS x FROM chunks WHERE library_id = %s LIMIT 1", (lib,)):
        raise HTTPException(400, "Your Library is empty - add your course files first."
                            if lib != db.DEMO else "The sample Library has not been loaded yet.")
    _ai()
    check_rate_limit(request, "ask")
    turns = [{"question": t.question, "answer": {"final_answer": t.final_answer}} for t in body.history[-HISTORY_TURNS:]]

    def events():
        for event in pipeline.stream(lib, q, turns):
            yield json.dumps(event) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
