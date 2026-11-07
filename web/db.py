"""The demo's tables, in Neon Postgres with pgvector (DATABASE_URL).

  files   one row per file in a Library: its name, format, pages and the readable
          text citations open (page markers or numbered lines, as chunks cite it)
  chunks  the searchable passages: text, a 768-dim embedding and a full-text
          index. Deleting a file deletes its chunks (ON DELETE CASCADE).

Two kinds of Library share the tables, and every query filters on library_id
(record 0001: one Library's question never reaches another's material):

  demo         the open-licence sample Library, loaded once by
               scripts/load_demo_library.py; read-only for visitors
  s:<uuid>     one browser's uploads, keyed by the anonymous session id it keeps
               in localStorage. Anyone holding the id can see those files. They
               are deleted UPLOAD_TTL_HOURS after upload (cleanup runs on requests).
"""
import os
import threading
import time

DIMS = 768
DEMO = "demo"
UPLOAD_TTL_HOURS = float(os.environ.get("UPLOAD_TTL_HOURS", "24"))

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS files (
    id          serial PRIMARY KEY,
    library_id  text NOT NULL,
    name        text NOT NULL,          -- the name chunks cite
    format      text NOT NULL,          -- PDF | Text
    pages       int NOT NULL,
    body        text NOT NULL,          -- the readable copy a citation opens
    created     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (library_id, name)
);
CREATE INDEX IF NOT EXISTS files_library ON files (library_id, created);
CREATE TABLE IF NOT EXISTS chunks (
    library_id text NOT NULL,
    chunk_id   text NOT NULL,
    file_id    int NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    file       text NOT NULL,
    loc        text,
    heading    text,
    text       text NOT NULL,
    embedding  vector({DIMS}) NOT NULL,
    -- what keyword search reads: the same header + text that is embedded
    tsv        tsvector GENERATED ALWAYS AS (
                   to_tsvector('english', file || ' ' || coalesce(loc, '') || ' ' || text)) STORED,
    PRIMARY KEY (library_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv);
"""

_ready = False
_ready_lock = threading.Lock()
_last_cleanup = 0.0


def configured():
    return bool(os.environ.get("DATABASE_URL", "").strip())


def connect():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(os.environ["DATABASE_URL"].strip(), autocommit=True, row_factory=dict_row)


def init():
    """Create the tables once per process (first request), not at import: the app
    starts and answers /api/health even before DATABASE_URL is set."""
    global _ready
    if _ready:
        return
    with _ready_lock:
        if not _ready:
            with connect() as conn:
                conn.execute(SCHEMA)
            _ready = True


def one(sql, args=()):
    init()
    with connect() as conn:
        return conn.execute(sql, args).fetchone()


def all_(sql, args=()):
    init()
    with connect() as conn:
        return conn.execute(sql, args).fetchall()


def run(sql, args=()):
    init()
    with connect() as conn:
        conn.execute(sql, args)


def many(sql, rows):
    init()
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, rows)


def cleanup(force=False):
    """Delete visitors' uploads older than UPLOAD_TTL_HOURS (their chunks go with them).
    Runs on ordinary requests, at most once a minute per instance: Cloud Run runs
    nothing between requests, so there is no timer."""
    global _last_cleanup
    now = time.monotonic()
    if not force and now - _last_cleanup < 60:
        return
    _last_cleanup = now
    run("DELETE FROM files WHERE library_id <> %s AND created < now() - make_interval(secs => %s)",
        (DEMO, UPLOAD_TTL_HOURS * 3600))
