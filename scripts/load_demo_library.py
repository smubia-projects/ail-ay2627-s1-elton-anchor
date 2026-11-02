"""Load the demo's sample Library (demo-library/) into Neon, once.

    pip install -r requirements-web.txt
    DATABASE_URL=postgresql://...?sslmode=require API_KEY=... python scripts/load_demo_library.py

Creates the tables if needed (web/db.py), replaces every file in the "demo"
Library, then reads, chunks and embeds each .md, .txt and .pdf in
demo-library/ (names starting with "_" are skipped) exactly as an upload is
read (web/ingest.py). Run it again after editing the files.

Uses the same env as the API: DATABASE_URL, API_KEY, API_BASE_URL and
EMBEDDING_MODEL_NAME (default text-embedding-3-small at 768 dims).
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

import db  # noqa: E402
import ingest  # noqa: E402

SRC = ROOT / "demo-library"


def main():
    if not db.configured():
        sys.exit("Set DATABASE_URL (the Neon connection string, with ?sslmode=require).")
    from openai_client import load_key
    if not load_key():
        sys.exit("Set API_KEY: the files' passages are embedded through the gateway.")
    files = sorted(p for p in SRC.iterdir()
                   if p.is_file() and not p.name.startswith("_") and ingest.fmt_of(p.name))
    if not files:
        sys.exit(f"No .md, .txt or .pdf files in {SRC}")
    db.run("DELETE FROM files WHERE library_id = %s", (db.DEMO,))
    for path in files:
        fmt = ingest.fmt_of(path.name)
        body, pages = ingest.read(path.name, fmt, path.read_bytes())
        row = ingest.add(db.DEMO, path.name, fmt, body, pages)
        n = db.one("SELECT count(*) AS n FROM chunks WHERE file_id = %s", (row["id"],))["n"]
        print(f"  {path.name}: {fmt}, {pages} page(s), {n} passage(s)")
    print(f"Loaded {len(files)} files into the '{db.DEMO}' Library.")


if __name__ == "__main__":
    main()
