"""Uploads in the demo: one file into one Library, read, chunked and indexed in the request.

The student's full pipeline (Docling layout and formula models, OCR for scans,
picture descriptions, spreadsheets in DuckDB) needs gigabytes of memory and
minutes per file, so the demo reads only what needs no local models:

  PDF      the text layer, page by page (pypdfium2), written as "## page N"
           sections so chunks and citations keep their page numbers.
           A PDF with no text layer (a scan) is refused.
  .md      Markdown, as written: chunks are cut at its headings.
  .txt     numbered lines, so citations name a line range.

Chunking is scripts/chunk.py's packing, the same as the eval Library (~800
tokens, whole blocks, ~100 overlap). Embeddings: the gateway's embedding model
at 768 dims (retrieval.embed).
"""
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import chunk as CH  # noqa: E402
import db  # noqa: E402
import retrieval  # noqa: E402

FORMATS = {".pdf": "PDF", ".txt": "Text", ".md": "Markdown"}
MAX_BYTES = 5 * 2**20
MAX_PAGES = 30           # per file
MAX_FILES = 3            # per visitor Library
MAX_CHARS = 200_000      # readable text per file
TEXT_LAYER_MIN = 20      # characters per page on average; fewer means a scan
WORDS_PER_PAGE = 500     # a text file's "pages", for the limit


class Refused(ValueError):
    """The file can't be added; the message says why, for the visitor."""


def fmt_of(name):
    return FORMATS.get(pathlib.Path(name).suffix.lower())


def read(name, fmt, data):
    """(body, pages): the readable copy citations open, and the page count."""
    if fmt == "PDF":
        import pypdfium2 as pdfium
        try:
            doc = pdfium.PdfDocument(io.BytesIO(data))
        except Exception:
            raise Refused(f"{name} could not be opened. Is it a valid PDF?")
        pages = len(doc)
        if pages > MAX_PAGES:
            raise Refused(f"{name} has {pages} pages; this demo reads up to {MAX_PAGES} per file.")
        texts = [doc[i].get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n") for i in range(pages)]
        if sum(len(t.strip()) for t in texts) < TEXT_LAYER_MIN * max(pages, 1):
            raise Refused(f"{name} looks like a scan (no text layer). This demo reads PDFs with selectable text only.")
        body = f"# {name}\n\n" + "\n\n".join(f"## page {i}\n\n{t.strip()}" for i, t in enumerate(texts, 1))
    else:
        text = data.decode("utf-8", errors="replace")
        if not text.strip():
            raise Refused(f"{name} is empty.")
        pages = max(1, -(-len(text.split()) // WORDS_PER_PAGE))
        if pages > MAX_PAGES:
            raise Refused(f"{name} is about {pages} pages long; this demo reads up to {MAX_PAGES} per file.")
        if fmt == "Text":
            body = "\n".join(f"{i:>4}  {ln}" for i, ln in enumerate(text.splitlines(), 1))
        else:
            body = text
    if len(body) > MAX_CHARS:
        raise Refused(f"{name} has more text than this demo reads ({MAX_CHARS:,} characters).")
    return body, pages


def make_chunks(name, fmt, body, file_id):
    """chunk.py's blocks and packing, as web/ingest.py did for uploads in the full app."""
    if fmt == "Text":
        blocks = CH.blocks_from_lines(body)
    else:
        blocks = CH.blocks_from_markdown(body, paged=fmt == "PDF")
    for b in blocks:
        b["tokens"] = CH.ntok(b["text"])
    blocks = [p for b in blocks for p in CH.split_oversized(b)]
    chunks = []
    for i, group in enumerate(CH.pack(blocks), 1):
        text = "\n\n".join(b["text"] for b in group)
        new = [b for b in group if not b.get("overlap")]
        loc = CH.location(group, "PDF" if fmt == "PDF" else "Text")["loc"]
        chunks.append({"chunk_id": f"f{file_id}#{i:03d}", "file": name, "loc": loc,
                       "heading": (new[0] if new else group[0])["heading"], "text": text})
    return [c for c in chunks if c["text"].strip()]


def add(library_id, name, fmt, body, pages):
    """Store a read file and index its chunks. Returns the file row."""
    row = db.one("INSERT INTO files (library_id, name, format, pages, body) VALUES (%s, %s, %s, %s, %s) "
                 "RETURNING id, name, format, pages, created", (library_id, name, fmt, pages, body))
    try:
        chunks = make_chunks(name, fmt, body, row["id"])
        if not chunks:
            raise Refused(f"{name} has no text to search.")
        for i in range(0, len(chunks), 100):
            batch = chunks[i:i + 100]
            vectors, _ = retrieval.embed([retrieval.chunk_input(c) for c in batch])
            db.many("INSERT INTO chunks (library_id, chunk_id, file_id, file, loc, heading, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)",
                    [(library_id, c["chunk_id"], row["id"], c["file"], c.get("loc"), c.get("heading"), c["text"], v)
                     for c, v in zip(batch, vectors)])
    except BaseException:
        db.run("DELETE FROM files WHERE id = %s AND library_id = %s", (row["id"], library_id))
        raise
    return row
